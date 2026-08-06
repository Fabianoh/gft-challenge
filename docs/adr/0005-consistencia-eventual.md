# ADR-005 — Consistência eventual no saldo consolidado

- **Status:** Aceito
- **Contexto de decisão:** modelo de consistência entre lançamento e saldo

## Contexto

O enunciado pede duas coisas que, em conjunto, determinam a resposta:

1. *"Serviço que faça o controle de lançamentos"* e *"serviço do consolidado diário"*
2. *"O serviço de controle de lançamento não deve ficar indisponível se o sistema
   de consolidado diário cair"*

Existe uma tensão real aqui, e ela precisa ser nomeada: **não existe desenho que
entregue isolamento total de falhas e saldo perfeitamente síncrono ao mesmo
tempo.** Essa é a formulação prática do teorema CAP. Escolher é obrigatório;
o que um arquiteto não pode fazer é deixar a escolha implícita.

## Decisão

**Consistência eventual** entre o lançamento e o saldo consolidado, com **janela
declarada e medida**: p95 < 30 segundos.

O lançamento é gravado com **consistência forte** (é a fonte da verdade). A
propagação para o saldo é assíncrona.

### Por que a escolha é forçada pelo requisito

Se o saldo fosse atualizado sincronamente no mesmo fluxo do registro:

- O registro passaria a depender da disponibilidade da apuração — **violando
  diretamente o requisito do enunciado**
- A latência do registro passaria a incluir a da apuração
- Um pico na apuração se propagaria para o caminho de receita

Ou seja: **o próprio requisito não-funcional do enunciado já decidiu por
consistência eventual.** A escolha aqui é apenas torná-la explícita, delimitada e
mensurável — em vez de acidental.

### O que a consistência eventual NÃO autoriza

Distinção que costuma ser confundida:

| Aceitável | Inaceitável |
|---|---|
| O saldo estar **atrasado** por alguns segundos | O saldo estar **errado** depois de convergir |
| Consulta durante a janela devolver posição anterior | Lançamento nunca chegar ao saldo |
| Relatório recém-emitido não incluir a última venda | Valor contabilizado em dobro |

**Consistência eventual é uma promessa sobre *quando*, não sobre *se*.** O saldo
pode chegar atrasado; não pode chegar errado nem deixar de chegar.

## Como a promessa é sustentada

| Garantia | Mecanismo |
|---|---|
| O evento não se perde | SQS com retentativa (3×) e DLQ de 14 dias |
| Reprocessamento não corrompe | Idempotência ([ADR-003](0003-comunicacao-assincrona.md)) |
| A janela é conhecida | SLI de defasagem, alarme em `ApproximateAgeOfOldestMessage` |
| Divergência é detectada | Job diário de reconciliação, publicando a diferença como métrica |
| O cliente sabe a idade do dado | Campo `ultima_atualizacao` em toda resposta de saldo |

O último item é frequentemente esquecido e é o que torna a consistência eventual
**honesta com o consumidor da API**: ele recebe, junto com o número, a informação
de quando aquele número foi apurado, e pode decidir se confia.

## Alternativas consideradas e descartadas

**Consistência forte via transação distribuída (two-phase commit).** Descartada:
acopla a disponibilidade dos dois serviços (violando o requisito), não é suportada
entre serviços AWS distintos, e degrada gravemente sob escala.

**Consistência forte via escrita síncrona nas duas tabelas.** Tecnicamente viável
com `TransactWriteItems` (ambas as tabelas estão no DynamoDB). Descartada porque
**recria o acoplamento no nível de dados**: o registro passaria a falhar quando a
tabela de consolidado tivesse problema. Trocaria acoplamento temporal por
acoplamento transacional — o requisito continuaria violado.

**Saldo calculado sob demanda, sem materialização.** Somar os lançamentos do dia
a cada consulta daria consistência forte por construção e eliminaria a
propagação. Descartada por não escalar: a 50 rps de leitura, cada consulta viraria
uma agregação sobre todos os lançamentos do período. É exatamente o problema
descrito no [ADR-004](0004-agregacao-incremental-vs-recalculo.md), só que no
caminho de leitura.

**Leitura do próprio escritor (read-your-writes).** Considerada como meio-termo:
o cliente que acabou de gravar veria o saldo atualizado. Descartada por
complexidade desproporcional ao benefício — exigiria roteamento por sessão e
estado adicional, para um caso de uso (comerciante consultando o saldo no
milissegundo seguinte ao registro) que não é real.

## Consequências

**Positivas**
- O requisito de isolamento é atendido de fato
- O registro tem latência mínima e previsível
- A apuração pode ser reprocessada, escalada ou reconstruída sem afetar o registro
- A fila absorve picos naturalmente

**Negativas**
- **O saldo pode estar desatualizado.** Mitigado por: janela curta e declarada,
  medição contínua, e `ultima_atualizacao` exposto na API.
- **Exige explicação ao negócio.** É a consequência mais subestimada: um
  comerciante que registra uma venda e não a vê no saldo imediatamente abre
  chamado. A mitigação é de produto, não técnica — a interface deve indicar
  "processando" enquanto a janela não fechou.
- **Reconciliação vira necessidade operacional**, não opcional.
- Depuração mais difícil: o estado do sistema é distribuído no tempo.

## Métrica que define o sucesso desta decisão

```
defasagem_saldo = ultima_atualizacao(saldo) − data_hora_evento(lançamento)
```

Publicada como métrica customizada (EMF) a cada consolidação. Se o p95 dessa
métrica ultrapassar 30 s de forma sustentada, **a promessa feita ao negócio foi
quebrada** — e a decisão precisa ser revisitada, seja aumentando a capacidade do
consumidor, seja renegociando a janela.
