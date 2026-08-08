# ADR-003 — Comunicação assíncrona por evento entre os domínios

- **Status:** Aceito, com correção pendente (ver §Problema conhecido)
- **Contexto de decisão:** como Lançamentos comunica a Consolidação

## Contexto

Requisito não-funcional explícito do enunciado:

> *"O serviço de controle de lançamento não deve ficar indisponível se o sistema
> de consolidado diário cair."*

Esse requisito elimina de saída qualquer chamada síncrona de Lançamentos para
Consolidação: numa chamada síncrona, a disponibilidade do chamador é o **produto**
da disponibilidade dos dois. Dois serviços de 99,9% acoplados sincronamente
entregam 99,8% — o registro passaria a depender da apuração, que é justamente o
que o requisito proíbe.

## Decisão

Comunicação **assíncrona por evento**, usando **SQS Standard** com DLQ como
transporte, e **EventBridge** como ponto de extensão para outros domínios.

```
Lançamentos ──publica──► SQS (consolidacao-queue) ──consome──► Consolidação
                              │
                              └── após 3 tentativas ──► DLQ (retenção 14 dias)
```

### Por que fila, e não notificação direta

| Critério | SQS | SNS | EventBridge | Kinesis |
|---|---|---|---|---|
| Absorve indisponibilidade do consumidor | ✅ Mensagem espera na fila | ❌ Perde se não houver assinante ativo | ⚠️ Precisa de DLQ configurada | ✅ Retém por período |
| Retentativa e DLQ nativas | ✅ | ⚠️ Via assinatura | ✅ | ⚠️ Manual |
| Custo em baixo volume | ✅ | ✅ | ✅ | ❌ Shard cobrado por hora |
| Ordenação | ❌ (Standard) | ❌ | ❌ | ✅ Por partição |
| Controle de vazão do consumidor | ✅ Batch + concorrência máxima | ❌ Empurra | ⚠️ Limitado | ✅ |

**SQS Standard vence** porque o atributo que mais importa aqui é *absorver
indisponibilidade do consumidor*, e é para isso que uma fila existe. Kinesis
seria a escolha se a ordenação estrita fosse necessária — não é, pelo motivo do
próximo parágrafo — e traria custo fixo por shard.

### Por que Standard e não FIFO

FIFO garante ordem e exatamente-uma-vez por grupo, mas limita a **300 mensagens/s
por grupo de mensagem**. Se o grupo fosse o comerciante, esse seria o teto de
escrita por comerciante.

E a ordem **não é necessária**: apuração de saldo é uma **soma**, e soma é
comutativa. Processar o crédito antes ou depois do débito produz o mesmo saldo
final. Pagar o preço da ordenação por uma garantia que o domínio não exige seria
desperdício.

O que Standard exige em troca é **idempotência**, tratada abaixo.

### Papel do EventBridge

O EventBridge **não** faz parte do caminho crítico. Ele é o barramento de
integração corporativo: o ponto por onde outros domínios — fiscal, BI,
antifraude, conciliação bancária — passam a consumir eventos de lançamento **sem
que Lançamentos precise saber que eles existem**.

É o que atende ao requisito de *"soluções escaláveis, reutilizáveis e flexíveis"*:
um novo consumidor é uma nova regra no barramento, não uma alteração no produtor.

> Hoje o EventBridge não tem consumidor. Isso é intencional (ponto de extensão
> preparado), mas **precisa ser dito** — caso contrário parece código morto.

## Idempotência: a contrapartida obrigatória

SQS Standard entrega **ao menos uma vez**. Reentrega é rotina, não exceção.

**Hoje** o consumidor recalcula o saldo do dia a partir da fonte, o que torna o
reprocessamento naturalmente idempotente (recalcular duas vezes dá o mesmo
resultado). Coberto por `test_reprocessar_a_mesma_mensagem_nao_altera_o_saldo`.

**Atenção na evolução:** ao migrar para agregação incremental
([ADR-004](0004-agregacao-incremental-vs-recalculo.md)), essa propriedade se
perde — `ADD` atômico não é idempotente e uma mensagem reentregue somaria o valor
duas vezes. A partir dali, a idempotência precisa ser **explícita**:

```python
transact_write_items([
    # marcador de deduplicação — falha se o evento já foi processado
    {"Put": {"Item": {"evento_id": ...}, 
             "ConditionExpression": "attribute_not_exists(evento_id)"}},
    # agregação — só se aplica junto com o marcador
    {"Update": {"UpdateExpression": "ADD total_creditos :valor"}},
])
```

A transação torna as duas operações atômicas: ou o evento é marcado **e** somado,
ou nada acontece. É assim que se obtém efeito de exatamente-uma-vez sobre um
transporte que entrega ao-menos-uma-vez.

## Problema conhecido: escrita dupla sem transação

O fluxo atual em `apps/lancamentos/operacoes.py` faz **três escritas
independentes** e não transacionais:

```python
lancamento_id = save_lancamento_to_dynamodb(validated_data)   # 1
send_to_consolidacao_queue(lancamento_id, validated_data)     # 2
send_to_eventbridge(lancamento_id, validated_data)            # 3
```

E o passo 2 **engole a exceção** (`apps/lancamentos/sqs.py`). Consequência: se o
SQS falhar, a API responde **201 Created**, o lançamento existe no banco e o
evento nunca é publicado. O saldo fica permanentemente errado, **sem nenhum
sinal** — nenhum alarme cobre esse caso, porque do ponto de vista do sistema nada
falhou.

Em domínio financeiro, perda silenciosa é o pior modo de falha possível: é pior
que uma falha ruidosa, porque ninguém sabe que precisa corrigir.

### Correção: outbox transacional via DynamoDB Streams

A tabela **já tem stream habilitado** (`StreamViewType: NEW_AND_OLD_IMAGES`) e
não o utiliza. Ele resolve exatamente este problema:

```
POST /lancamentos → Lambda → PutItem  (uma única escrita atômica)
                                 │
                                 ▼
                      DynamoDB Stream (garantido pelo serviço)
                                 │
                                 ▼
                      EventBridge Pipes → SQS → Consolidação
```

A publicação do evento passa a ser **consequência garantida** da gravação, e não
uma segunda operação que pode falhar isoladamente. Ganhos:

- Elimina a escrita dupla e a classe inteira de inconsistência
- O handler fica mais rápido (uma chamada de rede em vez de três)
- Ordenação por chave de partição vem de graça no stream

Coberto por `test_falha_ao_publicar_evento_nao_pode_responder_sucesso`.

## Consequências

**Positivas**
- Requisito de isolamento atendido no eixo temporal
- Fila absorve picos: o consumidor processa no seu ritmo
- DLQ preserva mensagens problemáticas por 14 dias para reprocessamento
- Novos consumidores entram sem alterar o produtor

**Negativas**
- **Consistência eventual** no saldo — tratada em [ADR-005](0005-consistencia-eventual.md)
- **Idempotência vira responsabilidade da aplicação**
- Depuração distribuída mais difícil — mitigada com X-Ray e correlação por id
- Mais peças móveis para operar e monitorar

## Lacuna operacional associada

O *event source mapping* usa `BatchSize: 10` **sem**
`FunctionResponseTypes: [ReportBatchItemFailures]`. Se a 10ª mensagem falhar, as
10 voltam para a fila e as 9 já processadas são processadas de novo. Com
agregação incremental, isso significa **contabilizar valores em dobro**.
Habilitar respostas parciais de lote é pré-requisito da migração do ADR-004.
