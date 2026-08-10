# API de Controle de Fluxo de Caixa

Referência dos endpoints REST para registro de lançamentos e consulta do saldo
consolidado.

> **Este documento descreve o comportamento real da implementação.** Onde a
> arquitetura alvo difere do que está implantado, há um aviso explícito. Documento
> de API que promete o que o código não entrega custa mais caro que documento
> nenhum — o integrador só descobre em produção.

---

## Visão geral

| | |
|---|---|
| Versão | v1 |
| Protocolo | HTTPS (TLS 1.2+) |
| Formato | JSON, UTF-8 |
| Base URL | `https://<api-id>.execute-api.<região>.amazonaws.com/<stage>` |
| Local | `http://localhost:3000` (ver [README](README.md#como-rodar-localmente)) |

### Autenticação

Todos os endpoints de negócio exigem um token JWT no cabeçalho `Authorization`:

```http
Authorization: Bearer <token>
```

A validação é feita por um Lambda Authorizer, que confere assinatura HMAC-SHA256,
emissor, audiência, expiração e **o escopo exigido pelo recurso**:

| Escopo | Concede |
|---|---|
| `lancamentos:write` | `POST /lancamentos` |
| `lancamentos:read` | `GET /lancamentos`, `GET /lancamentos/{id}` |
| `consolidado:read` | `GET /consolidado`, `GET /consolidado/relatorio` |
| `metricas:read` | `GET /metricas` |

Um token com apenas `lancamentos:write` registra vendas mas **não** lê o saldo
consolidado — é o que impede que um PDV comprometido numa loja exponha a posição
financeira do negócio inteiro.

Abertos sem token: `GET /health` e os `OPTIONS` de preflight CORS (o navegador
não envia `Authorization` no preflight).

**Para obter um token de teste:**

```bash
python scripts/gerar_token.py
```

Restringindo escopos, para demonstrar a autorização por capacidade:

```bash
python scripts/gerar_token.py --escopos lancamentos:write
```

> Em produção com provedor de identidade externo, o correto é RS256 com chaves
> públicas via JWKS. A escolha por HS256 com segredo no Secrets Manager mantém a
> stack autocontida; a troca é localizada em `_verificar_assinatura`.
> Ver [docs/07-seguranca.md](docs/07-seguranca.md#4-autenticação-e-autorização--o-desenho-alvo).

> ⚠️ **O ambiente local não aplica autenticação.** O roteador de
> `scripts/api_local.py` não invoca o authorizer, então os exemplos com `curl`
> sem token funcionam localmente mas não contra a API implantada.

### Limites de requisição

| Camada | Limite real | Observação |
|---|---|---|
| API Gateway — `/lancamentos` | **200 req/s** | Cota própria do caminho de receita |
| API Gateway — `/consolidado` | **80 req/s** | Acima dos 50 rps do requisito, com folga |
| WAF | **20.000 requisições por identidade a cada 5 minutos** (≈66 req/s) | Bloqueio, não throttling |
| WAF | 50.000 requisições por IP a cada 5 minutos | Rede de segurança contra flood |

> O rate limiting do WAF agrega pelo cabeçalho `Authorization`, e não por IP.
> Agregar por IP bloquearia um cliente legítimo de alto volume — uma rede de
> lojas saindo por um único NAT corporativo atingiria o limite muito antes dos
> 50 rps previstos no requisito não-funcional.

O throttling do API Gateway é **separado por serviço**: 200 rps em `/lancamentos`
e 80 rps em `/consolidado`. Assim um pico de leitura não consome a cota do
caminho de receita.

### Cabeçalhos

```http
Content-Type: application/json
Accept: application/json
```

| Cabeçalho | Estado | Uso |
|---|---|---|
| `Authorization: Bearer <jwt>` | ✅ **obrigatório** | Autenticação e autorização |
| `Idempotency-Key: <uuid>` | ❌ não implementado | Evitar duplicata em retentativa |
| `X-Correlation-Id: <uuid>` | ⚠️ não propagado | Rastreabilidade ponta a ponta |

### CORS

Respostas incluem `Access-Control-Allow-Origin: *`.

> ⚠️ Origem irrestrita não é adequada para API financeira. Deve ser lista
> explícita de domínios confiáveis.

---

## Endpoints

| Método | Recurso | Descrição |
|---|---|---|
| `GET` | `/health` | Verificação de disponibilidade |
| `POST` | `/lancamentos` | Registra débito ou crédito |
| `GET` | `/lancamentos` | Lista com filtros e paginação |
| `GET` | `/lancamentos/{id}` | Consulta individual |
| `GET` | `/consolidado` | Saldo diário |
| `GET` | `/consolidado/relatorio` | Relatório por período |
| `GET` | `/metricas` | Métricas agregadas |

---

## 1. Health Check

```http
GET /health
```

**200 OK**

```json
{
  "status": "healthy",
  "timestamp": "30/Jul/2025:10:00:00 +0000",
  "environment": "prod"
}
```

> ⚠️ **Este endpoint sempre responde `healthy`.** É uma integração MOCK do API
> Gateway, que devolve um valor fixo sem verificar DynamoDB, SQS ou Redis. Ele
> confirma apenas que o API Gateway está no ar.
>
> **Não use como sonda de dependências.** Um health check que nunca falha engana
> o monitoramento e o operador de plantão. Ver
> [docs/06-observabilidade.md](docs/06-observabilidade.md).

---

## 2. Criar lançamento

```http
POST /lancamentos
Content-Type: application/json
```

```json
{
  "tipo": "CREDITO",
  "valor": "1500.50",
  "descricao": "Pagamento recebido do cliente",
  "categoria": "VENDAS",
  "data": "2025-07-30T14:30:00Z",
  "tags": ["cliente-vip", "pagamento-antecipado"]
}
```

### Campos

| Campo | Tipo | Obrigatório | Regras |
|---|---|:---:|---|
| `tipo` | string | ✅ | `CREDITO` ou `DEBITO`, exatamente assim |
| `valor` | string ou número | ✅ | **Maior que zero.** O sinal do movimento vem do `tipo`, nunca do valor |
| `descricao` | string | ✅ | 1 a 255 caracteres; espaços nas pontas são removidos |
| `categoria` | string | ❌ | Padrão `GERAL` |
| `data` | string | ❌ | ISO 8601. Padrão: instante atual em **UTC** |
| `tags` | array | ❌ | Padrão `[]` |

> **Envie `valor` como string.** Um número JSON é lido como ponto flutuante pelo
> desserializador e já chega com a precisão comprometida. `"1500.50"` preserva o
> valor exato; `1500.50` não garante isso.

> ⚠️ O campo `metadata` **não é suportado**. Se enviado, é ignorado
> silenciosamente.

> ⚠️ **`data` é interpretada em UTC.** Uma venda às 22h no horário de Brasília é
> gravada como o dia seguinte e entra no saldo do dia errado. Correção prevista:
> campo `data_movimento` no fuso do comerciante. Ver
> [docs/02-requisitos-e-nfrs.md](docs/02-requisitos-e-nfrs.md#3-rastreabilidade-das-lacunas).

### 201 Created

```json
{
  "success": true,
  "message": "Lançamento criado com sucesso",
  "data": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "tipo": "CREDITO",
    "valor": 1500.5,
    "descricao": "Pagamento recebido do cliente",
    "data": "2025-07-30T14:30:00Z",
    "categoria": "VENDAS",
    "tags": ["cliente-vip", "pagamento-antecipado"]
  },
  "timestamp": "2025-07-30T14:30:01.123456+00:00"
}
```

> ⚠️ **`valor` retorna como número de ponto flutuante**, o que pode perder
> precisão. O valor gravado no banco é exato (`Decimal`); a perda ocorre apenas na
> serialização da resposta. Correção prevista: retornar como string.

### 400 Bad Request

```json
{
  "error": "Dados inválidos",
  "message": "Tipo deve ser 'DEBITO' ou 'CREDITO'"
}
```

Mensagens possíveis:

| Situação | `message` |
|---|---|
| Campo faltando | `Campo 'tipo' é obrigatório` |
| Tipo fora do domínio | `Tipo deve ser 'DEBITO' ou 'CREDITO'` |
| Valor ≤ 0 | `Valor deve ser maior que zero` |
| Valor não numérico | `Valor deve ser um número válido` |
| Descrição fora do limite | `Descrição deve ter entre 1 e 255 caracteres` |
| Data mal formatada | `Data deve estar no formato ISO 8601` |
| Body não é JSON | `Body da requisição deve ser um JSON válido` |

### Consistência eventual

O lançamento é gravado de forma síncrona e durável antes da resposta. **A
atualização do saldo consolidado é assíncrona**, com janela alvo de p95 abaixo de
30 segundos.

Consultar `/consolidado` imediatamente após um `POST` pode devolver a posição
anterior. Use o campo `ultima_atualizacao` da resposta para saber a idade do dado.
Ver [ADR-005](docs/adr/0005-consistencia-eventual.md).

### ⚠️ Sem idempotência

**Uma retentativa cria um segundo lançamento.** Não há deduplicação: se a resposta
se perder por timeout de rede e o cliente repetir a chamada, o valor é
contabilizado duas vezes.

Até que `Idempotency-Key` seja implementado, o consumidor precisa controlar isso
na origem — o que também vale como critério de integração
([docs/07-seguranca.md §5.1](docs/07-seguranca.md#51-requisitos-obrigatórios-do-consumidor)).

---

## 3. Listar lançamentos

```http
GET /lancamentos?data_inicio=2025-07-01&data_fim=2025-07-31&tipo=CREDITO&limit=50
```

### Parâmetros

| Parâmetro | Tipo | Padrão | Descrição |
|---|---|---|---|
| `data_inicio` | string | — | `YYYY-MM-DD` |
| `data_fim` | string | — | `YYYY-MM-DD` |
| `tipo` | string | — | `CREDITO` ou `DEBITO` |
| `categoria` | string | — | Correspondência exata |
| `tags` | string | — | Lista separada por vírgula |
| `limit` | inteiro | `50` | Entre 1 e 100 |
| `offset` | inteiro | `0` | Deslocamento |
| `sort` | string | `data_desc` | `data_desc`, `data_asc`, `valor_desc`, `valor_asc` |

### 200 OK

```json
{
  "success": true,
  "data": {
    "lancamentos": [
      {
        "id": "550e8400-e29b-41d4-a716-446655440000",
        "tipo": "CREDITO",
        "valor": 1500.5,
        "descricao": "Pagamento recebido do cliente",
        "data": "2025-07-30T14:30:00Z",
        "categoria": "VENDAS",
        "tags": ["cliente-vip"],
        "status": "ATIVO",
        "data_criacao": "2025-07-30T14:30:01.123456+00:00",
        "data_atualizacao": "2025-07-30T14:30:01.123456+00:00"
      }
    ],
    "pagination": {
      "total": 1,
      "limit": 50,
      "offset": 0,
      "has_more": false
    },
    "summary": {
      "total_creditos": 1500.5,
      "total_debitos": 0.0,
      "saldo_liquido": 1500.5,
      "quantidade_total": 1
    }
  },
  "timestamp": "2025-07-30T15:00:00.000000+00:00"
}
```

Somente lançamentos com `status: ATIVO` são retornados. Cancelamento é feito por
mudança de status, preservando a trilha de auditoria.

> ⚠️ **Desempenho.** Este endpoint executa uma varredura completa da tabela. O
> custo cresce com o volume total, não com o tamanho do resultado. Correção
> prevista em [ADR-002](docs/adr/0002-dynamodb-vs-relacional.md).

---

## 4. Obter lançamento por ID

```http
GET /lancamentos/550e8400-e29b-41d4-a716-446655440000
```

**200 OK** — objeto do lançamento em `data`.

| Código | Situação |
|---|---|
| 400 | ID não está no formato UUID |
| 404 | Lançamento inexistente |

---

## 5. Consultar saldo diário

```http
GET /consolidado?data=2025-07-30
```

### Parâmetros

| Parâmetro | Descrição |
|---|---|
| `data` | Data específica, `YYYY-MM-DD` |
| `data_inicio` + `data_fim` | Período; muda a resposta para `relatorio_periodo` |
| `incluir_detalhes` | `true` inclui o detalhamento diário (só com período) |
| `salvar_s3` | `true` arquiva o relatório no S3 |

Sem nenhum parâmetro, retorna o dia corrente com `"tipo": "saldo_atual"`.

### 200 OK

```json
{
  "success": true,
  "tipo": "saldo_diario",
  "data": {
    "data": "2025-07-30",
    "saldo_inicial": 5000.0,
    "total_creditos": 1500.5,
    "total_debitos": 300.25,
    "saldo_final": 6200.25,
    "quantidade_lancamentos": 12,
    "ultima_atualizacao": "2025-07-30T18:45:00.000000+00:00"
  },
  "timestamp": "2025-07-30T18:50:00.000000+00:00"
}
```

| Campo | Significado |
|---|---|
| `saldo_inicial` | Saldo final do dia anterior |
| `saldo_final` | `saldo_inicial + total_creditos − total_debitos` |
| `ultima_atualizacao` | **Quando o saldo foi apurado.** Use para avaliar a idade do dado |

Uma data sem movimento retorna **200 com valores zerados**, não 404: "não houve
movimento" é resposta de negócio válida e diferente de "a data não existe".

Saldo negativo é estado legítimo — o comerciante pode gastar mais do que recebeu
no dia.

---

## 6. Relatório por período

```http
GET /consolidado/relatorio?data_inicio=2025-07-01&data_fim=2025-07-31&incluir_detalhes=true
```

`data_inicio` e `data_fim` são **obrigatórios**.

### 200 OK

```json
{
  "success": true,
  "tipo": "relatorio_periodo",
  "data": {
    "periodo": {
      "inicio": "2025-07-01",
      "fim": "2025-07-31",
      "quantidade_dias": 31
    },
    "resumo_financeiro": {
      "saldo_inicial_periodo": 5000.0,
      "saldo_final_periodo": 18750.3,
      "total_creditos_periodo": 45200.8,
      "total_debitos_periodo": 31450.5,
      "variacao_liquida": 13750.3,
      "percentual_crescimento": 275.01
    }
  },
  "metadados": {
    "gerado_em": "2025-08-01T09:00:00.000000+00:00",
    "formato": "json",
    "incluir_detalhes": true
  },
  "timestamp": "2025-08-01T09:00:00.000000+00:00"
}
```

Com `incluir_detalhes=true`, `data.saldos_diarios` traz um objeto por dia.
Com `salvar_s3=true`, a resposta ganha `arquivos.s3_backup` com bucket e chave.

| Código | Situação |
|---|---|
| 400 | Parâmetro obrigatório ausente, data inválida, ou `data_inicio > data_fim` |

> ⚠️ **Períodos longos são caros.** Dias ainda não consolidados são calculados sob
> demanda, cada um disparando uma varredura de tabela. Um período de 12 meses pode
> exceder o timeout de 60 s da função.

---

## 7. Métricas agregadas

```http
GET /metricas?periodo=30d&incluir_tendencias=true
```

| Parâmetro | Valores | Padrão |
|---|---|---|
| `periodo` | `7d`, `30d`, `90d`, `1y` | `30d` |
| `incluir_tendencias` | `true`, `false` | `false` |

### 200 OK

```json
{
  "success": true,
  "data": {
    "periodo_analise": "30d",
    "periodo_datas": { "inicio": "2025-07-01", "fim": "2025-07-30", "dias_total": 30 },
    "resumo": {
      "total_lancamentos": 342,
      "total_creditos": 45200.8,
      "total_debitos": 31450.5,
      "saldo_liquido": 13750.3
    },
    "performance": {
      "dias_com_movimentacao": 28,
      "dias_sem_movimentacao": 2,
      "media_lancamentos_dia": 11.4,
      "maior_volume_dia": 4520.0,
      "menor_volume_dia": 120.5
    },
    "tendencias": {
      "direcao": "CRESCIMENTO",
      "intensidade": "MODERADA",
      "crescimento_medio_diario": 45.32
    }
  },
  "timestamp": "2025-07-30T20:00:00.000000+00:00"
}
```

`periodo` fora da lista retorna 400.

---

## Códigos de status

| Código | Quando |
|---|---|
| `200` | Consulta bem-sucedida |
| `201` | Lançamento criado |
| `400` | Payload ou parâmetro inválido |
| `404` | Recurso inexistente |
| `405` | Método não suportado para o recurso |
| `429` | Limite de requisições excedido |
| `500` | Erro interno |

| `401` | Token ausente, malformado, expirado ou com assinatura inválida |
| `403` | Token válido, mas sem o escopo exigido pelo recurso |

### Formato de erro

```json
{
  "error": "Dados inválidos",
  "message": "Tipo deve ser 'DEBITO' ou 'CREDITO'"
}
```

`error` é a categoria; `message` é a descrição acionável. Erros internos (500)
devolvem mensagem genérica de propósito, para não vazar detalhe de implementação.

---

## Recomendações de integração

| # | Recomendação | Por quê |
|---|---|---|
| 1 | Enviar `valor` como string | Preserva precisão monetária |
| 2 | Controlar duplicidade na origem | A API ainda não é idempotente |
| 3 | Recuo exponencial com *jitter* na retentativa | Evita tempestade de retentativas |
| 4 | Tratar 429 respeitando `Retry-After` | Throttling por serviço no gateway |
| 5 | Não assumir leitura imediata após escrita | Consistência eventual, p95 < 30 s |
| 6 | Ler `ultima_atualizacao` antes de confiar no saldo | Informa a idade do dado |
| 7 | Manter volume abaixo de ~66 req/s por identidade | Limite do WAF |

Critérios completos em
[docs/07-seguranca.md §5](docs/07-seguranca.md#5-critérios-de-segurança-para-integração-de-serviços).

---

## Documentos relacionados

| Documento | Conteúdo |
|---|---|
| [README](README.md) | Visão geral e execução local |
| [docs/03-arquitetura-alvo.md](docs/03-arquitetura-alvo.md) | Diagramas C4 e fluxos |
| [docs/07-seguranca.md](docs/07-seguranca.md) | Autenticação e critérios de integração |
| [docs/06-observabilidade.md](docs/06-observabilidade.md) | Métricas, alarmes e runbooks |
| [openapi_spec.txt](openapi_spec.txt) | Especificação OpenAPI |
