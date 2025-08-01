# API de Controle de Fluxo de Caixa

## 📋 Visão Geral

A API de Controle de Fluxo de Caixa é um serviço RESTful que permite gerenciar lançamentos financeiros (débitos e créditos) e consultar relatórios consolidados de saldo diário.

### Informações Básicas

- **Versão**: v1.0
- **Protocolo**: HTTPS
- **Formato**: JSON
- **Autenticação**: API Key (opcional)
- **Rate Limiting**: 1000 req/s por IP


### Headers Obrigatórios

'''http
Content-Type: application/json
Accept: application/json
'''

### Headers Opcionais

'''http
X-API-Key: sua-api-key-aqui
X-Request-ID: uuid-para-rastreamento
'''

---

## 🔗 Endpoints
# ############################################################################################################
### 1. Health Check
# ############################################################################################################
Verifica se a API está funcionando corretamente.

#### Request

'''http
GET /health
'''

#### Response

**Status Code**: `200 OK`

'''json
{
  "status": "healthy",
  "timestamp": "2025-07-30T10:00:00Z",
  "environment": "production",
  "version": "1.0.0",
  "services": {
    "database": "healthy",
    "cache": "healthy",
    "queue": "healthy"
  }
}
'''

#### Possíveis Status Codes

| Código | Descrição |
|--------|-----------|
| 200 | API funcionando normalmente |
| 503 | Serviços indisponíveis |

---

# ############################################################################################################
### 2. Criar Lançamento
# ############################################################################################################
Registra um novo lançamento (débito ou crédito) no sistema.

#### Request

'''http
POST /lancamentos
Content-Type: application/json

{
  "tipo": "CREDITO",
  "valor": 1500.50,
  "descricao": "Pagamento recebido do cliente",
  "categoria": "VENDAS",
  "data": "2025-07-30T14:30:00Z",
  "tags": ["cliente-vip", "pagamento-antecipado"],
  "metadata": {
    "cliente_id": "12345",
    "pedido_id": "PED-001"
  }
}
'''

#### Parâmetros do Body

| Campo | Tipo | Obrigatório | Descrição |
|-------|------|-------------|-----------|
| `tipo` | string | ✅ | Tipo do lançamento: `CREDITO` ou `DEBITO` |
| `valor` | number | ✅ | Valor do lançamento (positivo, até 2 casas decimais) |
| `descricao` | string | ✅ | Descrição do lançamento (1-255 caracteres) |
| `categoria` | string | ❌ | Categoria do lançamento (padrão: "GERAL") |
| `data` | string | ❌ | Data/hora ISO 8601 (padrão: agora) |
| `tags` | array | ❌ | Lista de tags para classificação |
| `metadata` | object | ❌ | Dados adicionais em formato chave-valor |

#### Response Success

**Status Code**: `201 Created`

'''json
{
  "success": true,
  "message": "Lançamento criado com sucesso",
  "data": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "tipo": "CREDITO",
    "valor": 1500.50,
    "descricao": "Pagamento recebido do cliente",
    "categoria": "VENDAS",
    "data": "2025-07-30T14:30:00Z",
    "tags": ["cliente-vip", "pagamento-antecipado"],
    "metadata": {
      "cliente_id": "12345",
      "pedido_id": "PED-001"
    },
    "data_criacao": "2025-07-30T14:30:01Z",
    "status": "ATIVO"
  },
  "timestamp": "2025-07-30T14:30:01Z"
}
'''

#### Response Error

**Status Code**: `400 Bad Request`

'''json
{
  "success": false,
  "error": "VALIDATION_ERROR",
  "message": "Dados inválidos fornecidos",
  "details": [
    {
      "field": "valor",
      "message": "Valor deve ser maior que zero",
      "code": "INVALID_VALUE"
    },
    {
      "field": "tipo",
      "message": "Tipo deve ser 'CREDITO' ou 'DEBITO'",
      "code": "INVALID_ENUM"
    }
  ],
  "timestamp": "2025-07-30T14:30:01Z"
}
'''

#### Possíveis Status Codes

| Código | Descrição |
|--------|-----------|
| 201 | Lançamento criado com sucesso |
| 400 | Dados inválidos |
| 422 | Erro de validação de negócio |
| 500 | Erro interno do servidor |

---

# ############################################################################################################
### 3. Listar Lançamentos
# ############################################################################################################
Recupera uma lista de lançamentos com filtros opcionais.

#### Request

'''http
GET /lancamentos?data_inicio=2025-07-01&data_fim=2025-07-31&tipo=CREDITO&categoria=VENDAS&limit=50&offset=0
'''

#### Query Parameters

| Parâmetro | Tipo | Obrigatório | Descrição |
|-----------|------|-------------|-----------|
| `data_inicio` | string | ❌ | Data início (YYYY-MM-DD) |
| `data_fim` | string | ❌ | Data fim (YYYY-MM-DD) |
| `tipo` | string | ❌ | Filtrar por tipo: `CREDITO` ou `DEBITO` |
| `categoria` | string | ❌ | Filtrar por categoria |
| `tags` | string | ❌ | Filtrar por tags (separadas por vírgula) |
| `limit` | integer | ❌ | Limite de resultados (padrão: 50, máx: 100) |
| `offset` | integer | ❌ | Offset para paginação (padrão: 0) |
| `sort` | string | ❌ | Ordenação: `data_asc`, `data_desc`, `valor_asc`, `valor_desc` |

#### Response

**Status Code**: `200 OK`

'''json
{
  "success": true,
  "data": {
    "lancamentos": [
      {
        "id": "550e8400-e29b-41d4-a716-446655440000",
        "tipo": "CREDITO",
        "valor": 1500.50,
        "descricao": "Pagamento recebido do cliente",
        "categoria": "VENDAS",
        "data": "2025-07-30T14:30:00Z",
        "tags": ["cliente-vip"],
        "data_criacao": "2025-07-30T14:30:01Z"
      }
    ],
    "pagination": {
      "total": 150,
      "limit": 50,
      "offset": 0,
      "has_more": true
    },
    "summary": {
      "total_creditos": 15000.00,
      "total_debitos": 8500.00,
      "saldo_liquido": 6500.00,
      "quantidade_total": 150
    }
  },
  "timestamp": "2025-07-30T14:30:01Z"
}
'''

---

# ############################################################################################################
### 4. Obter Lançamento por ID
# ############################################################################################################
Recupera um lançamento específico pelo seu ID.

#### Request

'''http
GET /lancamentos/{id}
'''

#### Path Parameters

| Parâmetro | Tipo | Descrição |
|-----------|------|-----------|
| `id` | string | UUID do lançamento |

#### Response

**Status Code**: `200 OK`

'''json
{
  "success": true,
  "data": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "tipo": "CREDITO",
    "valor": 1500.50,
    "descricao": "Pagamento recebido do cliente",
    "categoria": "VENDAS",
    "data": "2025-07-30T14:30:00Z",
    "tags": ["cliente-vip", "pagamento-antecipado"],
    "metadata": {
      "cliente_id": "12345",
      "pedido_id": "PED-001"
    },
    "data_criacao": "2025-07-30T14:30:01Z",
    "data_atualizacao": "2025-07-30T14:30:01Z",
    "status": "ATIVO"
  },
  "timestamp": "2025-07-30T14:30:01Z"
}
'''

#### Error Response

**Status Code**: `404 Not Found`

'''json
{
  "success": false,
  "error": "NOT_FOUND",
  "message": "Lançamento não encontrado",
  "timestamp": "2025-07-30T14:30:01Z"
}
'''

---

# ############################################################################################################
### 5. Consultar Saldo Diário
# ############################################################################################################
Obtém o saldo consolidado para uma data específica.

#### Request

'''http
GET /consolidado?data=2025-07-30
'''

#### Query Parameters

| Parâmetro | Tipo | Obrigatório | Descrição |
|-----------|------|-------------|-----------|
| `data` | string | ❌ | Data no formato YYYY-MM-DD (padrão: hoje) |
| `incluir_detalhes` | boolean | ❌ | Incluir detalhes dos lançamentos (padrão: false) |
| `refresh_cache` | boolean | ❌ | Forçar recálculo ignorando cache (padrão: false) |

#### Response

**Status Code**: `200 OK`

'''json
{
  "success": true,
  "tipo": "saldo_diario",
  "data": {
    "data": "2025-07-30",
    "saldo_inicial": 10000.00,
    "movimentacao": {
      "total_creditos": 5500.00,
      "total_debitos": 2300.00,
      "saldo_liquido": 3200.00
    },
    "saldo_final": 13200.00,
    "estatisticas": {
      "quantidade_lancamentos": 12,
      "quantidade_creditos": 8,
      "quantidade_debitos": 4,
      "ticket_medio_credito": 687.50,
      "ticket_medio_debito": 575.00
    },
    "categorias": {
      "VENDAS": {
        "creditos": 4500.00,
        "debitos": 0.00,
        "saldo": 4500.00
      },
      "DESPESAS": {
        "creditos": 0.00,
        "debitos": 1800.00,
        "saldo": -1800.00
      }
    },
    "ultima_atualizacao": "2025-07-30T15:30:00Z",
    "fonte_dados": "cache"
  },
  "timestamp": "2025-07-30T15:30:01Z"
}
'''

#### Com Detalhes

'''http
GET /consolidado?data=2025-07-30&incluir_detalhes=true
'''

'''json
{
  "success": true,
  "tipo": "saldo_diario",
  "data": {
    "data": "2025-07-30",
    "saldo_inicial": 10000.00,
    "saldo_final": 13200.00,
    "movimentacao": {
      "total_creditos": 5500.00,
      "total_debitos": 2300.00,
      "saldo_liquido": 3200.00
    },
    "lancamentos": [
      {
        "id": "550e8400-e29b-41d4-a716-446655440000",
        "tipo": "CREDITO",
        "valor": 1500.50,
        "descricao": "Pagamento cliente",
        "categoria": "VENDAS",
        "data": "2025-07-30T09:30:00Z"
      }
    ]
  }
}
'''

---

# ############################################################################################################
### 6. Relatório por Período
# ############################################################################################################
Gera relatório consolidado para um período de datas.

#### Request

'''http
GET /consolidado/relatorio?data_inicio=2025-07-01&data_fim=2027-07-31&formato=json&salvar_s3=true
'''

#### Query Parameters

| Parâmetro           | Tipo    | Obrigatório | Descrição |
|---------------------|---------|-------------|-----------|
| `data_inicio`       | string  |      ✅    | Data início (YYYY-MM-DD) |
| `data_fim`          | string  |      ✅    | Data fim (YYYY-MM-DD) |
| `formato`           | string  |      ❌    | Formato: `json`, `csv`, `pdf` (padrão: json) |
| `incluir_detalhes`  | boolean |      ❌    | Incluir saldos diários detalhados |
| `agrupar_por`       | string  |      ❌    | Agrupar por: `dia`, `semana`, `mes` |
| `salvar_s3`         | boolean |      ❌    | Salvar cópia no S3 para backup |
| `email_relatorio`   | string  |      ❌    | Enviar por email (formato PDF) |

#### Response

**Status Code**: `200 OK`

'''json
{
  "success": true,
  "tipo": "relatorio_periodo",
  "data": {
    "periodo": {
      "inicio": "2025-07-01",
      "fim": "2024-07-31",
      "dias_uteis": 22,
      "quantidade_dias": 31
    },
    "resumo_financeiro": {
      "saldo_inicial_periodo": 5000.00,
      "saldo_final_periodo": 25000.00,
      "total_creditos_periodo": 45000.00,
      "total_debitos_periodo": 25000.00,
      "variacao_liquida": 20000.00,
      "percentual_crescimento": 400.0
    },
    "estatisticas": {
      "dias_com_movimentacao": 28,
      "media_creditos_dia": 1607.14,
      "media_debitos_dia": 892.86,
      "maior_saldo_diario": 25000.00,
      "menor_saldo_diario": 5000.00,
      "dia_maior_movimentacao": "2025-07-25"
    },
    "categorias_periodo": {
      "VENDAS": {
        "total_creditos": 35000.00,
        "total_debitos": 0.00,
        "participacao_creditos": 77.8
      },
      "DESPESAS": {
        "total_creditos": 0.00,
        "total_debitos": 15000.00,
        "participacao_debitos": 60.0
      }
    },
    "tendencias": {
      "crescimento_medio_diario": 645.16,
      "volatilidade": "BAIXA",
      "sazonalidade": {
        "melhor_dia_semana": "quinta-feira",
        "pior_dia_semana": "domingo"
      }
    },
    "saldos_diarios": [
      {
        "data": "2025-07-01",
        "saldo_inicial": 5000.00,
        "total_creditos": 1200.00,
        "total_debitos": 300.00,
        "saldo_final": 5900.00,
        "quantidade_lancamentos": 5
      }
    ]
  },
  "metadados": {
    "gerado_em": "2025-07-01T10:00:00Z",
    "tempo_processamento_ms": 1250,
    "fonte_dados": "database",
    "versao_relatorio": "1.0"
  },
  "arquivos": {
    "s3_backup": {
      "bucket": "relatorios-fluxo-caixa",
      "key": "2025/07/relatorio_2025-07-01_2025-07-31_20240201100000.json",
      "url_presigned": "https://s3.amazonaws.com/..."
    }
  }
}
'''

---

# ############################################################################################################
### 7. Métricas Resumidas
# ############################################################################################################
Obtém métricas consolidadas do sistema.

#### Request

'''http
GET /metricas?periodo=30d&incluir_tendencias=true
'''

#### Query Parameters

| Parâmetro | Tipo | Descrição |
|-----------|------|-----------|
| `periodo` | string | Período: `7d`, `30d`, `90d`, `1y` |
| `incluir_tendencias` | boolean | Incluir análise de tendências |

#### Response

'''json
{
  "success": true,
  "data": {
    "periodo_analise": "30d",
    "resumo": {
      "total_lancamentos": 1250,
      "total_creditos": 125000.00,
      "total_debitos": 75000.00,
      "saldo_liquido": 50000.00,
      "crescimento_percentual": 15.5
    },
    "performance": {
      "media_lancamentos_dia": 41.7,
      "maior_volume_dia": 8500.00,
      "menor_volume_dia": 0.00,
      "dias_sem_movimentacao": 2
    },
    "tendencias": {
      "direcao": "CRESCIMENTO",
      "intensidade": "MODERADA",
      "previsao_proximo_mes": 57500.00,
      "confianca_previsao": 0.85
    }
  }
}
'''

---

# ############################################################################################################
## 📝 Códigos de Status HTTP
# ############################################################################################################
### Success Codes

| Código | Nome | Descrição |
|--------|------|-----------|
| 200 | OK | Requisição processada com sucesso |
| 201 | Created | Recurso criado com sucesso |
| 202 | Accepted | Requisição aceita para processamento assíncrono |

### Client Error Codes

| Código | Nome | Descrição |
|--------|------|-----------|
| 400 | Bad Request | Dados inválidos na requisição |
| 401 | Unauthorized | Autenticação necessária |
| 403 | Forbidden | Acesso negado |
| 404 | Not Found | Recurso não encontrado |
| 422 | Unprocessable Entity | Erro de validação de negócio |
| 429 | Too Many Requests | Rate limit excedido |

### Server Error Codes

| Código | Nome | Descrição |
|--------|------|-----------|
| 500 | Internal Server Error | Erro interno do servidor |
| 502 | Bad Gateway | Erro no gateway |
| 503 | Service Unavailable | Serviço temporariamente indisponível |
| 504 | Gateway Timeout | Timeout no gateway |

---

# ############################################################################################################
## 🔒 Autenticação e Segurança
# ############################################################################################################
### API Key Authentication

'''http
X-API-Key: sua-api-key-aqui
'''

### Rate Limiting

- **Limite Global**: 1000 req/s por IP
- **Limite por Endpoint**:
  - `POST /lancamentos`: 100 req/s
  - `GET /consolidado`: 200 req/s
  - `GET /consolidado/relatorio`: 10 req/s

### Headers de Rate Limit

'''http
X-RateLimit-Limit: 1000
X-RateLimit-Remaining: 999
X-RateLimit-Reset: 1642248000
'''

### CORS

'''http
Access-Control-Allow-Origin: *
Access-Control-Allow-Methods: GET, POST, PUT, DELETE, OPTIONS
Access-Control-Allow-Headers: Content-Type, Authorization, X-API-Key
'''

---

# ############################################################################################################
## 🚨 Tratamento de Erros
# ############################################################################################################
### Estrutura Padrão de Erro

'''json
{
  "success": false,
  "error": "ERROR_CODE",
  "message": "Descrição legível do erro",
  "details": [
    {
      "field": "campo_com_erro",
      "message": "Descrição específica",
      "code": "VALIDATION_CODE"
    }
  ],
  "timestamp": "2025-07-30T14:30:01Z",
  "request_id": "550e8400-e29b-41d4-a716-446655440000"
}
'''

### Códigos de Erro Comuns

| Código | Descrição |
|--------|-----------|
| `VALIDATION_ERROR` | Erro de validação de dados |
| `BUSINESS_RULE_ERROR` | Violação de regra de negócio |
| `RESOURCE_NOT_FOUND` | Recurso não encontrado |
| `DUPLICATE_RESOURCE` | Tentativa de criar recurso duplicado |
| `RATE_LIMIT_EXCEEDED` | Limite de taxa excedido |
| `SERVICE_UNAVAILABLE` | Serviço temporariamente indisponível |
| `INTERNAL_ERROR` | Erro interno do sistema |

---

# ############################################################################################################
## 📊 Monitoramento e Observabilidade
# ############################################################################################################
### Health Check Endpoints

'''http
GET /health                    # Status geral
GET /health/detailed          # Status detalhado de componentes
GET /health/database          # Status do banco de dados
GET /health/cache             # Status do cache
'''

### Métricas Disponíveis

- **Performance**: Latência, throughput, taxa de erro
- **Negócio**: Volume de transações, saldos, categorias
- **Infraestrutura**: CPU, memória, conexões DB

### Logging

Todos os requests são logados com:
- Request ID único
- Timestamp
- IP do cliente
- Método e endpoint
- Status code
- Tempo de resposta
- User agent