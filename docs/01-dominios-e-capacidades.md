# Domínios funcionais e capacidades de negócio

> Requisito obrigatório do desafio: *"Mapeamento de domínios funcionais e capacidades de negócio"*.

Este documento parte do negócio, não da tecnologia. A decomposição técnica
(serviços, tabelas, filas) é **consequência** do mapa abaixo, não o ponto de partida.

---

## 1. O negócio

Um comerciante precisa saber, ao final de cada dia, quanto entrou, quanto saiu e
com quanto ele ficou. Essa é a decisão de negócio que o sistema existe para
sustentar: **o comerciante fecha o caixa e confere se o número bate**.

Duas necessidades distintas se escondem nessa frase:

1. **Registrar o que aconteceu** — cada venda e cada pagamento precisam ser
   capturados no instante em que ocorrem, sem perda. Este é o fato bruto.
2. **Saber a posição** — a soma daqueles fatos, organizada por dia. Esta é uma
   interpretação dos fatos.

A distinção não é acadêmica: **elas têm requisitos de disponibilidade opostos**.
Se o registro falhar, a venda se perde e não há como recuperá-la — o fato
aconteceu no mundo e não foi capturado. Se a apuração falhar, nada se perde:
basta recalcular a partir dos fatos, que continuam lá.

É exatamente isso que o requisito não-funcional do enunciado formaliza:

> *"O serviço de controle de lançamento não deve ficar indisponível se o sistema
> de consolidado diário cair."*

**Essa frase não é um detalhe de infraestrutura: é a consequência direta da
diferença de criticidade entre registrar um fato e interpretá-lo.** Toda a
arquitetura decorre disso.

---

## 2. Mapa de capacidades de negócio

Capacidade = *o que* o negócio precisa saber fazer, independente de *como*.
A classificação segue Domain-Driven Design: **core** é onde a organização se
diferencia, **supporting** é necessário mas não diferencia, **generic** é
commodity que se compra pronto.

| # | Capacidade de negócio | Classificação | Por quê |
|---|---|---|---|
| C1 | **Registro de movimento financeiro** | **Core** | É o ativo do negócio. Um lançamento perdido é receita não contabilizada e não há como reconstruí-la. Justifica investimento em disponibilidade e durabilidade. |
| C2 | **Apuração de posição diária** | **Core** | É o produto entregue ao comerciante. Pode ser recalculada, então tolera indisponibilidade — mas não tolera estar *errada*. |
| C3 | Classificação e categorização | Supporting | Enriquece a análise (categoria, tags), mas o caixa fecha sem ela. |
| C4 | Consulta e relatórios | Supporting | Formas de expor C1 e C2. |
| C5 | Auditoria e rastreabilidade | Supporting | Exigência regulatória e de conciliação; não diferencia o negócio. |
| C6 | Autenticação e autorização | Generic | Resolvido com produto de mercado (Cognito/OIDC). Construir seria desperdício. |
| C7 | Observabilidade | Generic | CloudWatch/X-Ray. |
| C8 | Notificação e alerta | Generic | SNS. |

**Decisão que decorre do mapa:** só C1 e C2 justificam serviço próprio,
modelagem cuidadosa e orçamento de resiliência. C6–C8 são comprados prontos.
C3–C5 vivem dentro dos serviços de C1 e C2 — não merecem serviço próprio, porque
a granularidade de um microsserviço deve seguir a fronteira de negócio, não o
organograma nem a vontade de dividir.

---

## 3. Domínios funcionais (bounded contexts)

### 3.1 Contexto **Lançamentos** — o livro-razão

**Responsabilidade:** capturar e guardar, de forma imutável e auditável, cada
movimento financeiro que ocorre.

| | |
|---|---|
| Capacidades | C1, C3, C5 |
| Agregado raiz | `Lancamento` |
| Invariantes | valor sempre positivo (o sinal vem do `tipo`); tipo ∈ {CRÉDITO, DÉBITO}; lançamento nunca é apagado, apenas marcado como cancelado |
| Eventos publicados | `LancamentoCriado`, `LancamentoCancelado` |
| Consistência | **Forte.** É a fonte da verdade. |
| Disponibilidade alvo | 99,9% — caminho crítico de receita |

**Por que "nunca apagar":** o livro-razão é registro contábil. Correção se faz
por lançamento de estorno, não por edição do original — do contrário se perde a
trilha de auditoria e a capacidade de explicar como o saldo chegou onde chegou.

### 3.2 Contexto **Consolidação** — a posição apurada

**Responsabilidade:** manter e disponibilizar a posição financeira por dia,
derivada dos lançamentos.

| | |
|---|---|
| Capacidades | C2, C4 |
| Agregado raiz | `SaldoDiario` |
| Invariantes | `saldo_final = saldo_inicial + créditos − débitos`; `saldo_inicial` do dia D = `saldo_final` de D−1 |
| Eventos consumidos | `LancamentoCriado`, `LancamentoCancelado` |
| Consistência | **Eventual**, janela declarada de p95 < 30 s |
| Disponibilidade alvo | 99,5% — degradação tolerada por desenho |

**Este contexto não é dono de nenhum dado original.** Tudo que ele guarda é
derivado e reconstruível a partir dos eventos de Lançamentos. Essa propriedade é
o que permite tratá-lo como descartável: se a base do consolidado for perdida
inteira, ela se reconstrói reprocessando os lançamentos.

### 3.3 Context map — como os dois se relacionam

```
┌──────────────────────────┐                    ┌──────────────────────────┐
│      LANÇAMENTOS         │                    │      CONSOLIDAÇÃO        │
│   (upstream / fonte)     │                    │  (downstream / derivado) │
│                          │   LancamentoCriado │                          │
│  • registra o fato       │ ─────────────────► │  • apura a posição       │
│  • fonte da verdade      │      (assíncrono)  │  • dado derivado         │
│  • consistência forte    │                    │  • consistência eventual │
└──────────────────────────┘                    └──────────────────────────┘
          │                                                  │
          └─── dono exclusivo dos seus dados ────────────────┘
               (nenhum contexto lê a base do outro)
```

**Padrão de relacionamento:** *Publisher / Subscriber* com **Anticorruption
Layer** no consumidor.

Três propriedades sustentam o requisito não-funcional principal:

1. **A seta é de mão única.** Consolidação nunca chama Lançamentos. Se
   Consolidação cair inteira, Lançamentos não percebe.
2. **A seta é assíncrona.** Uma fila absorve indisponibilidade do consumidor;
   as mensagens esperam e são processadas quando ele voltar.
3. **Cada contexto é dono exclusivo dos seus dados.** Nenhum acessa a base do
   outro.

> ⚠️ **Divergência conhecida entre este desenho e o MVP implementado:** hoje o
> serviço de Consolidação lê a tabela de Lançamentos diretamente (`scan`),
> violando a propriedade 3. Isso é um acoplamento no nível de dados que ameaça o
> requisito não-funcional principal — as leituras do consolidado competem por
> capacidade na mesma tabela em que os lançamentos escrevem. Está registrado em
> [ADR-004](adr/0004-agregacao-incremental-vs-recalculo.md) e coberto pelo teste
> `tests/consolidado/test_lacunas_conhecidas.py::test_consolidado_nao_deve_acessar_a_tabela_de_lancamentos`.

---

## 4. Linguagem ubíqua

Termos com significado preciso e único em todo o sistema — código, API,
documentação e conversa com o negócio.

| Termo | Definição | Não confundir com |
|---|---|---|
| **Lançamento** | Registro imutável de um movimento financeiro ocorrido | "transação" (termo sobrecarregado: transação de banco de dados) |
| **Crédito** | Lançamento que aumenta o saldo (entrada) | O sentido contábil de "crédito", que é o oposto |
| **Débito** | Lançamento que reduz o saldo (saída) | idem |
| **Saldo inicial** | Posição na abertura do dia = saldo final do dia anterior | Saldo do primeiro lançamento |
| **Saldo final** | Posição no fechamento do dia | Saldo atual em tempo real |
| **Data de movimento** | Dia de negócio ao qual o lançamento pertence, no fuso do comerciante | Timestamp UTC do evento |
| **Consolidado** | Posição apurada de um dia | Relatório (que é a apresentação do consolidado) |

**Sobre "crédito" e "débito":** o enunciado do desafio usa os termos no sentido
coloquial — crédito entra, débito sai. Contabilmente é o inverso. A linguagem
ubíqua adota **o sentido do enunciado**, e este parágrafo existe para que a
ambiguidade nunca mais precise ser discutida.

**Sobre "data de movimento":** um lançamento tem *dois* tempos que não são o
mesmo — o instante em que o fato ocorreu (timestamp, UTC, absoluto) e o dia de
negócio a que ele pertence (data local do comerciante). Uma venda às 22h de 30/07
em São Paulo ocorre às 01h de 31/07 em UTC, mas pertence ao caixa do dia 30.
Tratar os dois como um só campo produz relatório que não bate com a gaveta.

> ⚠️ **Divergência conhecida:** o MVP usa UTC para ambos. Coberto pelo teste
> `test_venda_da_noite_pertence_ao_dia_local_do_comerciante`.

---

## 5. Do domínio para a arquitetura

A tabela fecha o raciocínio: cada decisão técnica é rastreável até uma
característica do negócio.

| Característica do domínio | Decisão arquitetural | ADR |
|---|---|---|
| C1 e C2 têm criticidade diferente | Dois serviços com ciclo de vida independente | [ADR-001](adr/0001-serverless-e-granularidade-dos-servicos.md) |
| Consolidado é derivado e reconstruível | Comunicação assíncrona por evento | [ADR-003](adr/0003-comunicacao-assincrona.md) |
| Lançamento não pode ser perdido | Fila com DLQ, retentativa e durabilidade | [ADR-003](adr/0003-comunicacao-assincrona.md) |
| Padrão de acesso conhecido e simples | DynamoDB (chave-valor) em vez de relacional | [ADR-002](adr/0002-dynamodb-vs-relacional.md) |
| Apuração é soma, e soma é comutativa | Agregação incremental em vez de recálculo | [ADR-004](adr/0004-agregacao-incremental-vs-recalculo.md) |
| Consolidado tolera atraso, não erro | Consistência eventual com janela declarada | [ADR-005](adr/0005-consistencia-eventual.md) |
| Carga com picos e vales | Serverless, escala sob demanda | [ADR-001](adr/0001-serverless-e-granularidade-dos-servicos.md) |
