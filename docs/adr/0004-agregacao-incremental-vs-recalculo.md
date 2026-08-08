# ADR-004 — Agregação incremental em vez de recálculo por varredura

- **Status:** Proposto (o MVP implementa a alternativa descartada)
- **Contexto de decisão:** como o saldo diário é apurado a cada evento
- **Prioridade:** P1 — sem esta mudança o RNF-02 não é atendível

## Contexto

O requisito não-funcional do enunciado estabelece a meta:

> *"Em dias de picos, o serviço de consolidado diário recebe 50 requisições por
> segundo, com no máximo 5% de perda de requisições."*

O MVP apura o saldo **recalculando o dia inteiro** a cada evento recebido.
Medindo o que isso custa por mensagem, em `apps/consolidado/sqs.py`:

| Passo | Operação | Custo |
|---|---|---|
| `get_saldo_anterior` | `get_item` no consolidado | 1 leitura |
| `calculate_saldo_diario` | `scan` na tabela de lançamentos | **1 varredura completa** |
| `save_saldo_diario` | `put_item` | 1 escrita |
| `invalidate_cache` | `KEYS` no Redis | **O(N), bloqueia o Redis** |
| `recalculate_subsequent_balances` | laço de até 30 dias × (`has_lancamentos` = 1 scan + `calculate_saldo_diario` = 1 scan) | **até 60 varreduras** |

**Um único lançamento pode disparar mais de 60 varreduras completas da tabela.**

A 50 req/s isso são ~3.000 varreduras por segundo. Numa tabela de 1 GB, cada
varredura consome ~125.000 RCUs. O Lambda tem `Timeout: 60` e estoura muito antes;
as mensagens vão para a DLQ e o custo de DynamoDB explode.

**O desenho atual não atende ao requisito — e não é questão de ajuste fino, é de
ordem de grandeza.** Trocar `scan` por `query` (o `#TODO` deixado no código)
reduz o custo de cada leitura, mas não elimina a amplificação de trabalho do
recálculo em cascata.

## Decisão

Substituir o recálculo por **agregação incremental com contador atômico**,
aplicando apenas o delta que já veio na mensagem.

```python
tabela_consolidado.update_item(
    Key={"comerciante_id": comerciante, "data_movimento": "2025-07-30"},
    UpdateExpression=(
        "ADD total_creditos :c, total_debitos :d, quantidade_lancamentos :n "
        "SET ultima_atualizacao = :ts"
    ),
    ExpressionAttributeValues={
        ":c": valor if tipo == "CREDITO" else Decimal("0"),
        ":d": valor if tipo == "DEBITO" else Decimal("0"),
        ":n": 1,
        ":ts": agora,
    },
)
```

**De O(N) varreduras por evento para O(1) escrita por evento.**

### Por que isso funciona: a propriedade do domínio

Apuração de saldo é uma **soma**, e soma é **associativa e comutativa**. Não
importa a ordem em que os lançamentos chegam nem se chegam juntos ou separados —
o total é o mesmo. Isso é o que permite aplicar deltas em vez de recalcular.

Sistemas que agregam operações não-comutativas (como "último valor vence") não
teriam essa liberdade. **A escolha arquitetural está apoiada numa propriedade
matemática do domínio, não numa preferência técnica.**

### O saldo acumulado passa a ser calculado na leitura

O contador guarda o movimento **do dia** (créditos, débitos, quantidade). O saldo
acumulado — que depende de todos os dias anteriores — passa a ser resolvido de
duas formas:

1. **Na leitura**, somando os deltas do período com um único `Query` por intervalo
   de chave de ordenação (barato, dado o modelo do [ADR-002](0002-dynamodb-vs-relacional.md))
2. **Materializado por um job diário** de fechamento, que grava o saldo de
   abertura do dia seguinte

Isso elimina completamente a cascata `recalculate_subsequent_balances`, que hoje
é a maior fonte de amplificação.

### A contrapartida: `ADD` não é idempotente

Esta é a consequência que **precisa** ser tratada junto, não depois.

Hoje o recálculo é naturalmente idempotente (recalcular duas vezes dá o mesmo
resultado). Com `ADD`, uma mensagem reentregue pelo SQS — que entrega ao menos
uma vez — **somaria o valor duas vezes**. Em domínio financeiro, inaceitável.

A solução é deduplicação transacional:

```python
dynamodb.transact_write_items(TransactItems=[
    {"Put": {
        "TableName": tabela_eventos_processados,
        "Item": {"evento_id": {"S": lancamento_id}, "ttl": {"N": str(expira_em_7_dias)}},
        "ConditionExpression": "attribute_not_exists(evento_id)",
    }},
    {"Update": {
        "TableName": tabela_consolidado,
        "UpdateExpression": "ADD total_creditos :c, total_debitos :d",
        # ...
    }},
])
```

Se o marcador já existe, a transação inteira é cancelada
(`TransactionCanceledException`) e a mensagem é descartada como duplicata. As duas
operações são atômicas: ou o evento é registrado **e** somado, ou nada acontece.

**Efeito de exatamente-uma-vez sobre um transporte que entrega ao-menos-uma-vez.**
O TTL de 7 dias limpa os marcadores automaticamente, sem job de manutenção.

## Mudança associada: separar os armazenamentos

O consumidor deixa de ler a tabela de Lançamentos. Isso corrige, junto, a
violação de bounded context descrita em
[domínios e capacidades](../01-dominios-e-capacidades.md): hoje as varreduras do
consolidado competem por capacidade na mesma tabela em que os lançamentos
escrevem, e um pico de leitura pode causar throttling na escrita — **exatamente o
cenário que o RNF-01 proíbe**.

Com agregação incremental, o consumidor usa apenas o delta que veio na mensagem.
Ele nunca precisa reler a origem.

## Alternativas consideradas e descartadas

**Trocar `scan` por `query` no GSI, mantendo o recálculo.** É o que o `#TODO` no
código sugere. Descartado porque não resolve: continua sendo O(N) no número de
lançamentos do dia, multiplicado pelos até 30 dias do recálculo em cascata. Reduz
a constante, não a ordem de grandeza. (E o GSI atual é inutilizável de qualquer
forma — ver [ADR-002](0002-dynamodb-vs-relacional.md).)

**Consolidar em lote, por janela de tempo.** Agrupar eventos e consolidar a cada
N segundos reduziria a escrita. Descartado como solução principal porque aumenta
a defasagem do saldo (SLO de p95 < 30 s) e adiciona estado intermediário. Continua
válido como **otimização futura** via `MaximumBatchingWindowInSeconds`, que já
agrupa mensagens no event source mapping.

**Materializar tudo por job noturno.** Descartado: o comerciante precisa ver o
saldo do dia corrente, não só o de ontem.

**Kinesis Data Analytics / Flink.** Descartado por desproporção — soma de valores
por dia não justifica um motor de processamento de fluxo.

## Consequências

**Positivas**
- Suporta os 50 rps do RNF-02 com folga (uma escrita por evento)
- Custo de DynamoDB cai em várias ordens de grandeza
- Elimina a cascata de recálculo
- Corrige o acoplamento de dados entre os bounded contexts
- `ADD` é atômico: some a condição de corrida entre mensagens concorrentes do
  mesmo dia (hoje duas mensagens simultâneas fazem *read-modify-write* e uma
  sobrescreve a outra)
- Corrige um bug latente: `scan` é **eventualmente consistente** por padrão, então
  o recálculo pode não enxergar o lançamento que disparou o evento, gravando um
  saldo errado que nunca mais é corrigido

**Negativas**
- Exige a tabela de deduplicação (custo e complexidade adicionais)
- Correção de lançamento passa a exigir **evento de estorno**, em vez de
  recálculo — o que, aliás, é a prática contábil correta
- Perde-se a auto-cura do recálculo: se um evento for perdido, o saldo fica
  errado até uma reconciliação. **Mitigação:** job diário de reconciliação que
  compara a soma dos lançamentos com o consolidado e publica a divergência como
  métrica de negócio — o canário de correção do sistema

## Verificação

```
tests/consolidado/test_lacunas_conhecidas.py::test_um_evento_nao_pode_disparar_varredura_da_tabela
tests/consolidado/test_lacunas_conhecidas.py::test_consolidado_nao_deve_acessar_a_tabela_de_lancamentos
```

Ambos contam os acessos à tabela de lançamentos durante o processamento de um
evento e exigem **zero**. Estão marcados como `xfail(strict=True)`: passam a
falhar por XPASS no momento em que a correção entrar, forçando a atualização.

## Pré-requisitos da migração

1. Habilitar `FunctionResponseTypes: [ReportBatchItemFailures]` no event source
   mapping — sem isso, uma falha no lote reprocessa mensagens já aplicadas e
   duplica valores ([ADR-003](0003-comunicacao-assincrona.md))
2. Corrigir a modelagem de chave ([ADR-002](0002-dynamodb-vs-relacional.md))
3. Trocar `KEYS` por chaves versionadas no cache
4. Implantar o job de reconciliação **antes** de remover o recálculo
