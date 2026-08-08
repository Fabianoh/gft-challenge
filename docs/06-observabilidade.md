# Monitoramento e observabilidade

> Requisito **diferencial** do desafio: *"Monitoramento e Observabilidade"*.

Os SLIs e SLOs que dão base a tudo aqui estão em
[requisitos e NFRs §2.2](02-requisitos-e-nfrs.md#22-slis-e-slos). Este documento
trata de **como** eles são medidos e do que acontece quando são violados.

---

## 1. O princípio que organiza este documento

> **Sem SLO, todo limiar de alarme é chute.** Por que alarmar em 5 erros e não em
> 3? Por que 10 segundos de latência e não 2? Um alarme que ninguém sabe
> justificar é um alarme que será silenciado no terceiro disparo — e a partir daí
> o sistema está sem monitoramento, só que ninguém percebeu.

Todo alarme neste documento é derivado de um SLO, e todo SLO é derivado de uma
característica de negócio.

**Segundo princípio:** monitorar **sintoma**, não causa. O usuário não se importa
com o uso de CPU; ele se importa com "a venda foi registrada?" e "o saldo está
certo?". Alarmes de causa geram ruído; alarmes de sintoma geram ação.

---

## 2. Os três pilares, e o quarto

| Pilar | Ferramenta | Responde a |
|---|---|---|
| **Métricas** | CloudWatch | *Existe um problema agora?* |
| **Logs** | CloudWatch Logs | *O que exatamente aconteceu nesta requisição?* |
| **Rastros** | X-Ray | *Onde, na cadeia distribuída, o tempo foi gasto?* |
| **Reconciliação** | Job diário + métrica de negócio | *O resultado está **correto**?* |

O quarto pilar não aparece na literatura clássica, e é o mais importante neste
domínio. Os três primeiros detectam **falha**; nenhum deles detecta um sistema
que responde 200, com latência boa e sem erro nos logs, **entregando um saldo
errado**. Em sistema financeiro, esse é o pior modo de falha, e só a
reconciliação o pega. Ver §6.

---

## 3. O que existe hoje

| Recurso | Estado |
|---|---|
| X-Ray com `TracingConfig: Active` nas duas Lambdas + camada do SDK | ✅ |
| Dashboard no CloudWatch | ✅ |
| Alarme de erros nas Lambdas (> 5 em 10 min) | ✅ |
| Alarme de 4XX e 5XX no API Gateway | ✅ |
| Alarme de latência no API Gateway (> 10 s) | ✅ |
| Alarme de throttling no DynamoDB | ✅ |
| Alarme de profundidade da fila (> 100 mensagens) | ✅ |
| Tópico SNS com assinatura por e-mail | ✅ |

É uma base decente — melhor que a maioria das entregas de desafio. As lacunas
abaixo, porém, deixam o sistema cego exatamente onde ele é mais frágil.

---

## 4. Lacunas críticas

### 4.1 Não existe alarme na DLQ

**É a lacuna mais grave deste documento.** Uma mensagem na DLQ significa um
lançamento que **nunca entrou no saldo** — o comerciante tem um número errado no
relatório e ninguém foi avisado.

Hoje o único alarme de fila é `ApproximateNumberOfVisibleMessages > 100` na fila
**principal**, que mede acúmulo, não falha definitiva.

```yaml
DLQAlarm:
  Type: AWS::CloudWatch::Alarm
  Properties:
    MetricName: ApproximateNumberOfMessagesVisible
    Namespace: AWS/SQS
    Dimensions: [{ Name: QueueName, Value: !Ref ConsolidacaoDLQ }]
    Statistic: Sum
    Period: 60
    EvaluationPeriods: 1
    Threshold: 0
    ComparisonOperator: GreaterThanThreshold   # qualquer mensagem já é incidente
    TreatMissingData: notBreaching
```

O limiar é **zero**, e isso é deliberado: não existe quantidade aceitável de
lançamentos perdidos.

### 4.2 Não existe alarme de defasagem do saldo

`ApproximateAgeOfOldestMessage` é o SLI que traduz a promessa de consistência
eventual ([ADR-005](adr/0005-consistencia-eventual.md)): *"o saldo está no máximo
30 segundos atrasado"*.

**Profundidade de fila não substitui isso.** Uma fila com 5 mensagens paradas há
20 minutos é muito pior que uma fila com 500 escoando em 3 segundos — e o alarme
atual só enxergaria a segunda.

### 4.3 Não existe alarme de concorrência nem de throttling de Lambda

`Throttles` e `ConcurrentExecutions` são o sinal direto do modo de falha do
RNF-01: o consolidado drenando a cota de concorrência da conta e deixando os
lançamentos sem execução. Hoje esse cenário aconteceria **sem nenhum aviso** —
apareceria só como 429 para o cliente.

### 4.4 Os logs de acesso do API Gateway não são entregues

Em `infra/cfn/apigateway.yml`, o `AccessLogSetting` tem o `Format` definido mas o
**`DestinationArn` está comentado**. O log que carrega `requestId`, IP, status e
latência não vai para lugar nenhum — a observabilidade fica cega na borda,
justamente onde se investiga "de onde veio esta requisição".

### 4.5 Sem retenção de logs

Nenhum log group tem `RetentionInDays`. Os logs são retidos **para sempre**, e o
custo cresce indefinidamente. 30 dias para operação, com exportação para S3 do
que precisar de retenção fiscal.

### 4.6 Sem métricas de negócio

Todas as métricas atuais são de infraestrutura. Nenhuma responde a uma pergunta
de negócio. Ver §5.

---

## 5. Catálogo de alarmes proposto

Cada alarme tem SLO de origem, limiar justificado e ação esperada.

### Severidade 1 — acorda alguém

| Alarme | Condição | SLO de origem | Ação |
|---|---|---|---|
| **DLQ com mensagem** | `> 0` por 1 min | Durabilidade do evento = 100% | Investigar a mensagem, corrigir a causa, reprocessar |
| **5XX em Lançamentos** | taxa `> 1%` por 5 min | Disponibilidade 99,9% | Caminho de receita comprometido |
| **Throttle de Lambda** | `> 0` por 5 min | Isolamento (RNF-01) | Verificar se o consolidado drenou a concorrência |
| **Divergência na reconciliação** | `≠ 0` | Corretude do saldo | Saldo errado em produção |

### Severidade 2 — horário comercial

| Alarme | Condição | SLO de origem | Ação |
|---|---|---|---|
| **Defasagem do saldo** | `ApproximateAgeOfOldestMessage > 30 s` por 5 min | Janela de consistência p95 < 30 s | Aumentar concorrência do consumidor |
| **Latência p95 de Lançamentos** | `> 200 ms` por 10 min | SLO de latência | Investigar partida a frio ou DynamoDB |
| **Throttling no DynamoDB** | `> 0` por 5 min | Capacidade | Verificar partição quente |
| **Orçamento de erro consumido** | `> 80%` do mês | Gestão de SLO | Congelar features, priorizar confiabilidade |

### Severidade 3 — registro para análise

| Alarme | Condição | Por quê |
|---|---|---|
| **4XX elevado** | `> 5%` por 15 min | Pode indicar cliente mal integrado ou tentativa de abuso |
| **Bloqueios no WAF** | pico anômalo | Possível ataque, ou **falso positivo bloqueando cliente legítimo** |
| **Custo diário** | acima do previsto | Detecta varredura descontrolada ou laço infinito |

> **O alarme de custo é subestimado.** No desenho atual, o recálculo em cascata
> ([ADR-004](adr/0004-agregacao-incremental-vs-recalculo.md)) poderia gerar
> milhares de dólares em varreduras de DynamoDB antes de alguém perceber. Um
> alarme de anomalia de custo é a rede de segurança financeira do sistema.

### Alarmes compostos

Alarme isolado gera ruído; alarme composto gera diagnóstico:

```
IncidenteConsolidacao = (DLQ > 0) OR (Defasagem > 60s) OR (ErrosConsolidado > 1%)
```

Uma notificação com significado — *"a consolidação está degradada"* — em vez de
três e-mails que o time precisa correlacionar às 3h da manhã.

---

## 6. Métricas de negócio (EMF)

O *Embedded Metric Format* permite publicar métricas customizadas escrevendo no
log — sem chamada de API adicional, sem latência no caminho crítico.

| Métrica | Pergunta que responde | Dimensões |
|---|---|---|
| `LancamentosRegistrados` | O volume está normal para este dia/hora? | tipo, categoria |
| `ValorTransacionado` | O ticket médio mudou? | tipo |
| `DefasagemSaldoSegundos` | **A promessa de consistência está sendo cumprida?** | — |
| `DivergenciaReconciliacao` | **O saldo está correto?** | comerciante |
| `EventosDuplicadosDescartados` | A deduplicação está funcionando? | — |
| `LancamentosPorComerciante` | Algum cliente está fora do padrão? | comerciante |

**`DefasagemSaldoSegundos`** é a métrica que operacionaliza o
[ADR-005](adr/0005-consistencia-eventual.md):

```
defasagem = ultima_atualizacao(saldo) − data_hora_evento(lançamento)
```

Sem ela, a promessa de "p95 abaixo de 30 segundos" é uma frase em documento, não
um compromisso verificado.

### O job de reconciliação — o canário de correção

Roda diariamente e, para cada comerciante:

1. Soma independentemente os lançamentos ATIVOs do dia
2. Compara com o consolidado gravado
3. Publica a diferença como `DivergenciaReconciliacao`
4. Divergência ≠ 0 dispara severidade 1

**É o único mecanismo que detecta saldo errado.** Métricas de infraestrutura,
logs e rastros mostrariam um sistema perfeitamente saudável enquanto ele entrega
um número incorreto.

Ele também é **pré-requisito** da migração para agregação incremental: ao trocar
recálculo por deltas, perde-se a auto-cura do recálculo — um evento perdido passa
a produzir erro permanente. A reconciliação precisa estar em produção **antes**
dessa mudança, não depois.

---

## 7. Logs

### Formato

Log estruturado em JSON, nunca texto livre. Texto livre não é consultável.

```json
{
  "timestamp": "2025-07-30T14:30:00.123Z",
  "level": "INFO",
  "service": "lancamentos",
  "correlation_id": "req-abc123",
  "lancamento_id": "550e8400-...",
  "comerciante_id": "COM#4711",
  "event": "lancamento_registrado",
  "duration_ms": 43
}
```

### Correlação ponta a ponta

O `correlation_id` nasce no `requestId` do API Gateway, atravessa o Lambda de
lançamentos, viaja como atributo da mensagem SQS e reaparece no consolidado.
**É o que permite responder "o que aconteceu com a venda das 14h32?" em uma
consulta**, em vez de garimpar dois log groups por horário aproximado.

### O que nunca pode aparecer no log

| Proibido | Por quê |
|---|---|
| Token, chave de API, credencial | Log é lido por mais gente do que se imagina |
| Payload completo de requisição | Pode conter dado pessoal (LGPD) |
| Dado pessoal identificável | Minimização de dados |

> ⚠️ **Hoje `main.lambda_handler` registra o evento inteiro** —
> `config.logger.info(f"Evento recebido: {json.dumps(event)}")` — incluindo
> headers e body. Se houver `Authorization` no header, **o token vai para o
> CloudWatch em texto claro**. Isso precisa ser corrigido junto com a
> implementação de autenticação ([segurança §4](07-seguranca.md)).

---

## 8. Rastreamento distribuído

X-Ray está ativo nas duas funções, com subsegmentos anotados via
`@xray_recorder.capture` nas operações relevantes.

**O que ele responde bem:** onde o tempo é gasto dentro de uma requisição, e qual
dependência degradou.

**A limitação neste desenho:** o rastro **quebra na fronteira assíncrona**. O
rastro do POST termina quando a mensagem entra na fila; o do consolidado começa
do zero. Para costurar os dois, é preciso propagar o cabeçalho de rastreamento
como atributo da mensagem SQS e reabrir o segmento no consumidor.

**Amostragem:** 100% em desenvolvimento; 5–10% em produção. Rastrear tudo em
volume alto custa caro e não agrega diagnóstico proporcional
([custos §5.5](05-estimativa-custos.md#55-amostragem-no-x-ray)).

---

## 9. Painéis

Três públicos, três painéis. Um painel que serve a todos não serve a ninguém.

**Painel de negócio** — para o comerciante e a liderança
Lançamentos por hora · valor transacionado · saldo consolidado do dia ·
divergência da reconciliação.

**Painel operacional** — para quem está de plantão
Taxa de erro e latência por endpoint · profundidade e idade da fila · DLQ ·
concorrência e throttles · consumo do orçamento de erro.

**Painel de diagnóstico** — para investigação
Mapa de serviços do X-Ray · latência por dependência · partida a frio · RCU/WCU
consumidos · taxa de acerto do cache.

---

## 10. Runbooks

Alarme sem runbook transfere o problema para quem está de plantão às 3h da manhã.

### "DLQ com mensagem"

1. Inspecionar a mensagem: `aws sqs receive-message --queue-url <dlq>`
2. Localizar o rastro pelo `correlation_id`
3. Classificar a causa:
   - **Dado malformado** → corrigir na origem; a mensagem não deve ser reprocessada
   - **Dependência indisponível** → confirmar recuperação e reprocessar
   - **Defeito de código** → corrigir, implantar, reprocessar
4. Reprocessar via redrive do SQS
5. Confirmar com a reconciliação que o saldo do dia convergiu

### "Defasagem do saldo acima de 30 s"

1. Verificar `ApproximateNumberOfMessagesVisible` — é acúmulo ou lentidão?
2. Verificar `Throttles` do consolidado — a concorrência está limitando?
3. Verificar `Duration` — a função ficou mais lenta?
4. Verificar throttling do DynamoDB
5. Mitigação imediata: elevar `MaximumConcurrency` do event source mapping
6. Comunicar: se a defasagem passar de 5 min, o negócio precisa saber que o
   relatório está atrasado

### "Divergência na reconciliação"

1. **Não corrigir o consolidado manualmente.** Descobrir a causa primeiro — o
   consolidado é derivado, e sobrescrevê-lo esconde o defeito.
2. Identificar quais lançamentos divergem
3. Verificar DLQ e eventos descartados como duplicata
4. Só depois de entendida a causa, recalcular o dia a partir dos lançamentos

---

## 11. O que falta implementar

| # | Item | Prioridade |
|---|---|---|
| 1 | Alarme de DLQ | **P1** |
| 2 | Alarme de `ApproximateAgeOfOldestMessage` | **P1** |
| 3 | Descomentar `DestinationArn` do access log | **P1** |
| 4 | Alarmes de `Throttles` e `ConcurrentExecutions` | P1 |
| 5 | `RetentionInDays` em todos os log groups | P1 |
| 6 | Parar de registrar o evento inteiro (vazamento de token) | **P1 — segurança** |
| 7 | Métricas de negócio via EMF | P2 |
| 8 | Job de reconciliação | P2 — **pré-requisito do ADR-004** |
| 9 | Correlação de rastro através do SQS | P2 |
| 10 | Alarmes compostos e painéis segmentados | P3 |
| 11 | Canário sintético (CloudWatch Synthetics) | P3 |
