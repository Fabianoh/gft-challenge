# ADR-002 — DynamoDB como armazenamento primário

- **Status:** Aceito, com ressalva de modelagem (ver §Consequências)
- **Contexto de decisão:** escolha do banco de dados dos dois domínios

## Contexto

Os dois domínios têm padrões de acesso conhecidos e estreitos:

**Lançamentos**
- Gravar um lançamento (alto volume, chave única)
- Buscar por identificador
- Listar por período, tipo e categoria

**Consolidação**
- Gravar/atualizar o saldo de um dia
- Buscar o saldo de um dia
- Buscar saldos de um intervalo de dias

Não há consulta ad-hoc, junção entre entidades nem agregação analítica complexa
no caminho quente. **Os padrões de acesso são conhecidos de antemão** — condição
que decide a escolha.

## Decisão

**Amazon DynamoDB** em modo *on-demand* (`PAY_PER_REQUEST`), com criptografia KMS
e *point-in-time recovery* habilitados.

| Critério | DynamoDB | Aurora Serverless v2 (PostgreSQL) |
|---|---|---|
| Latência sob escala | ✅ Milissegundo de dígito único, previsível | ⚠️ Degrada com volume e concorrência |
| Escala de escrita | ✅ Praticamente ilimitada, automática | ⚠️ Limitada pela instância de escrita |
| Custo em repouso | ✅ Zero (on-demand) | ❌ Piso mínimo de ACU sempre ativo |
| Gestão de conexão a partir de Lambda | ✅ HTTP, sem pool | ❌ Exige RDS Proxy (custo e latência extras) |
| Consulta ad-hoc / relatório analítico | ❌ Fraco | ✅ SQL completo |
| Transação multi-entidade | ⚠️ Limitada (até 100 itens) | ✅ ACID completo |
| Flexibilidade a mudança de acesso | ❌ Exige remodelagem ou novo índice | ✅ Índice novo resolve |

A decisão pende para DynamoDB por três razões, nesta ordem:

1. **Custo em repouso zero** casa com o perfil de picos e vales do enunciado.
   Aurora cobraria capacidade mínima 24×7 mesmo à noite.
2. **Sem pool de conexões.** O maior atrito de banco relacional com Lambda é o
   esgotamento de conexões sob escala — cada execução concorrente abre a sua.
   Resolver exige RDS Proxy, que adiciona custo, latência e mais uma peça a operar.
3. **Latência previsível sob escala**, que é o que sustenta o SLO de p95 < 200 ms
   no registro.

## Alternativas consideradas e descartadas

**Aurora Serverless v2.** Seria a escolha certa se o requisito incluísse
relatórios analíticos ad-hoc ou transações multi-linha frequentes. Foi descartada
pelo custo de piso e pelo atrito de conexão — não por ser inferior.

**DocumentDB / MongoDB.** Descartado: traz o custo operacional de um cluster sem
oferecer, para este caso, vantagem sobre DynamoDB.

**Timestream (série temporal).** Considerado por lançamento ser dado temporal.
Descartado porque o domínio exige leitura transacional por identificador e
atualização de status — Timestream é otimizado para ingestão e agregação, não
para acesso por chave.

## Consequências

**Positivas**
- Escala e latência adequadas sem ajuste manual
- Custo proporcional ao uso
- Criptografia, PITR e Streams nativos
- **Streams** habilitam a evolução para outbox transacional ([ADR-003](0003-comunicacao-assincrona.md))

**Negativas**
- **Rigidez de modelagem.** Um padrão de acesso não previsto exige novo índice ou
  remodelagem. É o preço direto da latência previsível.
- **Sem consulta analítica.** Mitigação: arquivar no S3 e consultar com Athena
  quando surgir a necessidade de BI. Separar o caminho transacional do analítico
  é desejável de qualquer forma.
- **Aritmética financeira exige cuidado.** DynamoDB devolve `Decimal` via boto3;
  converter para `float` em qualquer ponto do caminho perde precisão. Coberto por
  `test_valor_e_gravado_como_decimal_no_banco`.

## Ressalva: a modelagem atual está incorreta

A escolha do DynamoDB está certa; **a modelagem entregue no MVP não está**, e a
distinção importa porque o risco de DynamoDB é justamente errar a chave.

### Problema 1 — O índice secundário é inutilizável

`infra/cfn/dynamodb.yml` cria o índice `data-tipo-index` com chave de partição
`data`. Mas o atributo `data` guarda o **timestamp ISO completo**
(`2025-07-30T14:30:00Z`). Chave de partição só aceita **igualdade exata**, então
é impossível consultar "todos os lançamentos de 30/07". O índice existe, encarece
toda escrita e nunca pode ser usado — por isso o código caiu em `scan`.

### Problema 2 — Não existe o comerciante na chave

Nenhuma tabela tem `comerciante_id`. O sistema só funciona para um único
comerciante; com dois, os saldos se somam e vazam entre eles.

### Modelagem correta

| Tabela | Chave de partição | Chave de ordenação | Consulta que habilita |
|---|---|---|---|
| Lançamentos | `COMERCIANTE#<id>` | `LANC#<data_movimento>#<uuid>` | "lançamentos do comerciante X entre D1 e D2" com um único `Query` por intervalo |
| Consolidado | `COMERCIANTE#<id>` | `DIA#<YYYY-MM-DD>` | "saldos de julho do comerciante X" com um único `Query` |

Com essa chave, **nenhum índice secundário é necessário** e nenhum `scan`
acontece. O isolamento entre comerciantes passa a ser garantido pela chave, não
por filtro de aplicação — o que também evita vazamento por bug de código.

**Sobre partição quente:** usar `data_movimento` como chave de partição
concentraria todas as escritas do dia numa única partição (limite de 1.000 WCU).
Prefixar pelo comerciante distribui naturalmente. Em cenário de comerciante único
com volume muito alto, a saída é *write sharding* — sufixo de shard na chave
(`DIA#2025-07-30#03`) com leitura agregando os shards.

Correção rastreada em [ADR-004](0004-agregacao-incremental-vs-recalculo.md).
