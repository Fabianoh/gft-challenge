# Arquitetura alvo — visão C4

> Requisito obrigatório do desafio: *"Desenho da solução completo (Arquitetura Alvo)"*.

Os diagramas seguem o modelo [C4](https://c4model.com), do mais abstrato para o
mais concreto. Estão em Mermaid, então renderizam direto no GitHub — sem
depender de ferramenta externa nem de imagem que envelhece separada do texto.

> Existe também uma versão em draw.io com os ícones oficiais da AWS em
> [`diagramas/arquitetura-fluxo-de-caixa.drawio`](diagramas/arquitetura-fluxo-de-caixa.drawio),
> mantida como **fonte editável** para apresentação. Os diagramas Mermaid abaixo
> são a visão canônica: um `.drawio` não renderiza no GitHub, e um diagrama que
> exige download para ser visto acaba não sendo visto.

**Convenção usada em todos os diagramas:**
linha cheia = síncrono · linha tracejada = assíncrono ·
componente pontilhado = arquitetura alvo, ainda não implementado.

---

## Nível 1 — Contexto

Quem usa o sistema e com o que ele conversa. Sem nenhum detalhe de tecnologia:
este é o diagrama que se mostra para um interlocutor de negócio.

```mermaid
flowchart TB
    comerciante["👤 Comerciante<br/><i>registra movimentos e<br/>confere o caixa do dia</i>"]
    operador["👤 Operador de PDV<br/><i>registra vendas<br/>durante o expediente</i>"]

    sistema["<b>Sistema de Controle<br/>de Fluxo de Caixa</b><br/><br/><i>Registra lançamentos e apura<br/>a posição diária consolidada</i>"]

    pdv["🏪 PDV / Frente de Caixa<br/><i>sistema externo</i>"]
    erp["📊 ERP / Contabilidade<br/><i>sistema externo</i>"]
    bi["📈 BI e Analytics<br/><i>sistema externo</i>"]
    fiscal["🧾 Sistema Fiscal<br/><i>sistema externo</i>"]

    operador -->|"registra venda"| pdv
    pdv -->|"envia lançamento<br/>HTTPS/JSON"| sistema
    comerciante -->|"consulta saldo e<br/>relatórios HTTPS"| sistema

    sistema -.->|"publica eventos<br/>de lançamento"| erp
    sistema -.->|"publica eventos"| bi
    sistema -.->|"publica eventos"| fiscal

    classDef externo fill:#8b98a8,stroke:#5a6472,color:#fff
    classDef nucleo fill:#1c6ea4,stroke:#0d4d78,color:#fff
    classDef pessoa fill:#2d6a4f,stroke:#1b4332,color:#fff
    class pdv,erp,bi,fiscal externo
    class sistema nucleo
    class comerciante,operador pessoa
```

**O que este diagrama comunica:** o sistema é **fonte de eventos** para o resto
da organização, não um silo. ERP, BI e fiscal consomem sem que o fluxo de caixa
precise conhecê-los — é o que sustenta o requisito de *"soluções reutilizáveis e
flexíveis"* do enunciado.

---

## Nível 2 — Contêineres

As unidades implantáveis e como se comunicam.

```mermaid
flowchart TB
    cliente["👤 Cliente<br/><i>PDV ou navegador</i>"]

    subgraph borda["Borda"]
        cdn["CloudFront + S3<br/><i>frontend estático</i>"]
        waf["AWS WAF<br/><i>rate limiting, OWASP</i>"]
        gw["API Gateway REST<br/><i>entrada única, validação,<br/>throttling por método</i>"]
        authz["Lambda Authorizer<br/><i>valida JWT e escopos</i>"]
    end

    subgraph ctxLanc["Bounded Context: Lançamentos"]
        fnLanc["Lambda Lançamentos<br/><i>Python 3.x</i><br/><b>registra o fato</b>"]
        dbLanc[("DynamoDB<br/>lançamentos<br/><i>fonte da verdade</i>")]
        stream[["DynamoDB Stream<br/><i>outbox transacional</i>"]]
    end

    subgraph transporte["Transporte de eventos"]
        pipe["EventBridge Pipes"]
        fila[["SQS<br/>consolidacao-queue"]]
        dlq[["SQS<br/>DLQ · 14 dias"]]
        bus{{"EventBridge Bus<br/><i>integração corporativa</i>"}}
    end

    subgraph ctxCons["Bounded Context: Consolidação"]
        fnCons["Lambda Consolidado<br/><i>Python 3.x</i><br/><b>apura a posição</b>"]
        dbCons[("DynamoDB<br/>consolidado")]
        dbDedup[("DynamoDB<br/>eventos-processados<br/><i>TTL 7 dias</i>")]
        cache[("ElastiCache Redis<br/><i>cache de leitura</i>")]
        s3[("S3<br/><i>relatórios arquivados</i>")]
    end

    subgraph obs["Observabilidade"]
        cw["CloudWatch<br/><i>logs, métricas, alarmes</i>"]
        xray["X-Ray<br/><i>tracing distribuído</i>"]
    end

    cliente --> cdn
    cliente --> waf
    waf --> gw
    gw -.->|"autoriza"| authz
    gw -->|"POST /lancamentos"| fnLanc
    gw -->|"GET /consolidado"| fnCons

    fnLanc -->|"PutItem<br/><b>escrita única</b>"| dbLanc
    dbLanc -.-> stream
    stream -.-> pipe
    pipe -.-> fila
    pipe -.-> bus
    fila -.->|"falha 3x"| dlq
    fila -.->|"batch de 10"| fnCons

    fnCons -->|"UpdateItem ADD<br/>+ dedup transacional"| dbCons
    fnCons --> dbDedup
    fnCons <-->|"cache aside"| cache
    fnCons -->|"arquiva"| s3

    fnLanc -.-> cw
    fnCons -.-> cw
    fnLanc -.-> xray
    fnCons -.-> xray

    classDef alvo stroke-dasharray: 6 4,fill:#3d4654,stroke:#8b98a8,color:#fff
    classDef lanc fill:#1c6ea4,stroke:#0d4d78,color:#fff
    classDef cons fill:#7a4b9e,stroke:#54307a,color:#fff
    classDef dados fill:#2d6a4f,stroke:#1b4332,color:#fff
    class authz,stream,pipe,bus,dbDedup,cdn alvo
    class fnLanc lanc
    class fnCons cons
    class dbLanc,dbCons,cache,s3 dados
```

### O que sustenta o requisito de isolamento

O único caminho entre os dois contextos é **a fila**, e a seta é de mão única.
Isso resolve o acoplamento *temporal* — mas não basta. Quatro recursos ainda
precisam ser segregados:

| Recurso | Controle necessário |
|---|---|
| Concorrência Lambda | `ReservedConcurrentExecutions` no produtor (piso garantido) + `ScalingConfig.MaximumConcurrency` no consumidor (teto imposto) |
| Cota do API Gateway | `MethodSettings` por `ResourcePath`, não um limite único no stage |
| Base de dados | Cada contexto dono exclusiva da sua tabela |
| Permissão IAM | Uma role por função |

> ⚠️ **Estado atual:** os quatro estão compartilhados. Detalhe em
> [requisitos e NFRs §2.1](02-requisitos-e-nfrs.md#21-os-dois-requisitos-explícitos-do-enunciado).

---

## Nível 3 — Componentes

### Lambda Lançamentos

```mermaid
flowchart LR
    evento(["Evento do<br/>API Gateway"])

    subgraph fn["Lambda Lançamentos"]
        direction TB
        handler["<b>main.lambda_handler</b><br/><i>roteamento por método<br/>e recurso</i>"]
        oper["<b>operacoes</b><br/><i>orquestra o caso de uso</i>"]
        valid["<b>utils.validate_lancamento</b><br/><i>regras de negócio</i>"]
        exc["<b>excecoes</b><br/><i>erros de domínio tipados</i>"]
        idem["<b>idempotencia</b><br/><i>Idempotency-Key</i>"]
        repo["<b>dynamodb</b><br/><i>persistência</i>"]
        cfg["<b>configuration</b><br/><i>ambiente e clientes</i>"]
    end

    tabela[("DynamoDB<br/>lançamentos")]

    evento --> handler
    handler --> oper
    oper --> valid
    valid -.->|"levanta"| exc
    oper --> idem
    oper --> repo
    repo --> tabela
    cfg -.-> repo

    classDef alvo stroke-dasharray: 6 4,fill:#3d4654,stroke:#8b98a8,color:#fff
    classDef nucleo fill:#1c6ea4,stroke:#0d4d78,color:#fff
    class idem alvo
    class valid,oper nucleo
```

**`utils.validate_lancamento` é o coração do domínio** — é onde vivem as
invariantes (valor positivo, tipo no domínio, descrição no limite). Por ser
função pura, sem dependência de infraestrutura, é testável sem mock: 27 dos 94
testes atacam exclusivamente ela.

### Lambda Consolidado

```mermaid
flowchart LR
    sqsEvt(["Evento SQS<br/><i>batch de 10</i>"])
    httpEvt(["Evento HTTP<br/><i>API Gateway</i>"])

    subgraph fn["Lambda Consolidado"]
        direction TB
        handler["<b>main.lambda_handler</b><br/><i>discrimina SQS de HTTP</i>"]
        consumo["<b>sqs</b><br/><i>consumo e idempotência</i>"]
        rota["<b>utils.route_http_request</b><br/><i>roteamento de consulta</i>"]
        calc["<b>operacoes</b><br/><i>agregação e relatório</i>"]
        cacheOps["<b>redis_ops</b><br/><i>cache aside,<br/>degradação graciosa</i>"]
        recon["<b>reconciliacao</b><br/><i>job diário de conferência</i>"]
    end

    dbCons[("DynamoDB<br/>consolidado")]
    redis[("Redis")]

    sqsEvt --> handler
    httpEvt --> handler
    handler --> consumo
    handler --> rota
    consumo --> calc
    rota --> calc
    calc --> dbCons
    calc <--> cacheOps
    cacheOps <--> redis
    recon -.-> dbCons

    classDef alvo stroke-dasharray: 6 4,fill:#3d4654,stroke:#8b98a8,color:#fff
    classDef nucleo fill:#7a4b9e,stroke:#54307a,color:#fff
    class recon alvo
    class calc,consumo nucleo
```

**Uma função, dois gatilhos.** O mesmo deployable atende a fila e o HTTP,
discriminando pelo formato do evento. É uma decisão de simplicidade consciente —
e tem um custo que vale nomear: um pico de consultas e um pico de consolidação
disputam a mesma cota de concorrência. Se a leitura e a escrita passarem a ter
perfis de carga muito diferentes, separar em duas funções é o próximo passo.

---

## Fluxos

### Registro de lançamento — caminho feliz

```mermaid
sequenceDiagram
    autonumber
    participant C as PDV
    participant G as API Gateway
    participant A as Authorizer
    participant L as Lambda Lançamentos
    participant D as DynamoDB lançamentos
    participant S as Stream + Pipes
    participant Q as SQS

    C->>G: POST /lancamentos<br/>Authorization + Idempotency-Key
    G->>A: valida token
    A-->>G: permitido · escopo lancamentos:write
    G->>G: valida schema do payload
    G->>L: evento proxy

    L->>L: valida regras de negócio
    Note over L: valor > 0 · tipo no domínio<br/>Decimal, nunca float

    L->>D: PutItem condicional<br/>attribute_not_exists idempotency_key
    D-->>L: gravado

    L-->>G: 201 Created
    G-->>C: 201 · id do lançamento

    Note over D,Q: A partir daqui é assíncrono.<br/>O cliente já foi respondido.
    D->>S: registro no stream
    S->>Q: publica LancamentoCriado
```

**O ponto de virada é o passo 8.** A resposta ao cliente sai assim que o fato
está durável, e a propagação acontece depois. Se o cliente tivesse que esperar a
consolidação, a disponibilidade do registro passaria a ser o **produto** das duas
— violando o requisito do enunciado.

**O `PutItem` é a única escrita.** A publicação do evento é consequência
garantida dela, via stream. É o que elimina a escrita dupla descrita no
[ADR-003](adr/0003-comunicacao-assincrona.md).

### Consolidação assíncrona

```mermaid
sequenceDiagram
    autonumber
    participant Q as SQS
    participant L as Lambda Consolidado
    participant T as Tabela dedup
    participant D as DynamoDB consolidado
    participant R as Redis
    participant W as CloudWatch

    Q->>L: batch de até 10 eventos

    loop cada evento
        L->>L: extrai delta<br/>tipo, valor, data_movimento

        critical transação atômica
            L->>T: Put condicional<br/>attribute_not_exists evento_id
            L->>D: UpdateItem ADD<br/>total_creditos / total_debitos
        option evento já processado
            T-->>L: TransactionCanceledException
            L->>L: descarta duplicata
        end

        L->>R: invalida chave versionada
        L->>W: publica defasagem_saldo
    end

    alt todos processados
        L-->>Q: sucesso · mensagens removidas
    else falha parcial
        L-->>Q: batchItemFailures<br/><i>só as que falharam retornam</i>
        Note over Q: após 3 tentativas → DLQ<br/>alarme dispara
    end
```

**Os dois detalhes que fazem esse fluxo funcionar:**

**A transação (passos 3–4).** `ADD` é atômico mas **não idempotente** — uma
reentrega somaria o valor duas vezes. Amarrar o contador ao marcador de
deduplicação numa transação dá efeito de *exatamente-uma-vez* sobre um transporte
que entrega *ao-menos-uma-vez*.

**O `batchItemFailures`.** Sem ele, uma falha na décima mensagem devolve as dez à
fila e as nove já aplicadas são somadas de novo. Com agregação incremental, isso
é **valor contabilizado em dobro**.

### Consulta de saldo com cache

```mermaid
sequenceDiagram
    autonumber
    participant C as Comerciante
    participant G as API Gateway
    participant L as Lambda Consolidado
    participant R as Redis
    participant D as DynamoDB consolidado

    C->>G: GET /consolidado?data=2025-07-30
    G->>L: evento proxy
    L->>R: GET saldo:comerciante:data:v3

    alt cache quente
        R-->>L: posição
    else cache frio ou indisponível
        R--xL: miss ou timeout
        Note over L,R: falha de cache nunca<br/>propaga como erro
        L->>D: GetItem
        D-->>L: posição
        L->>R: SETEX ttl 3600
    end

    L-->>G: 200 · saldo + ultima_atualizacao
    G-->>C: resposta

    Note over C: ultima_atualizacao informa a idade<br/>do dado — honestidade sobre<br/>consistência eventual
```

**O cache nunca é caminho crítico.** Redis fora do ar degrada latência, não
disponibilidade — comportamento já implementado em `redis_ops.get_redis_client`.

**`ultima_atualizacao` na resposta** é o que torna a consistência eventual
honesta: o cliente recebe o número **e** quando ele foi apurado, e decide se
confia.

---

## Delta entre o alvo e o implementado

```mermaid
flowchart TB
    subgraph hoje["MVP implementado"]
        h1["Lambda grava no banco<br/>e publica na fila<br/><b>escrita dupla</b>"]
        h2["Consolidado varre a tabela<br/>de Lançamentos<br/><b>+60 scans por evento</b>"]
        h3["API sem autenticação<br/><b>AuthorizationType NONE</b>"]
        h4["Role IAM única<br/>Concorrência compartilhada"]
    end

    subgraph alvo["Arquitetura alvo"]
        a1["PutItem único<br/>Stream publica o evento<br/><b>outbox transacional</b>"]
        a2["UpdateItem ADD atômico<br/>+ dedup transacional<br/><b>O de 1 por evento</b>"]
        a3["Authorizer JWT<br/>escopos por endpoint"]
        a4["Role por função<br/>Concorrência reservada"]
    end

    h1 ==>|"ADR-003"| a1
    h2 ==>|"ADR-004"| a2
    h3 ==>|"P1 · segurança"| a3
    h4 ==>|"P1 · isolamento"| a4

    classDef atual fill:#8b3a3a,stroke:#5c2424,color:#fff
    classDef futuro fill:#2d6a4f,stroke:#1b4332,color:#fff
    class h1,h2,h3,h4 atual
    class a1,a2,a3,a4 futuro
```

Cada seta corresponde a um teste `xfail(strict=True)` já escrito: quando a
correção entrar, o teste passa e o pytest exige a atualização do status.

```bash
python tarefas.py lacunas
```

---

## Modelo de dados alvo

Tabela única por contexto, com chave composta. Ver
[ADR-002](adr/0002-dynamodb-vs-relacional.md) para a justificativa e a crítica ao
modelo atual.

| Tabela | PK | SK | Atributos |
|---|---|---|---|
| `lancamentos` | `COMERCIANTE#<id>` | `LANC#<data_movimento>#<uuid>` | tipo, valor (Decimal), descricao, categoria, tags, status, data_hora_evento, idempotency_key |
| `consolidado` | `COMERCIANTE#<id>` | `DIA#<YYYY-MM-DD>` | total_creditos, total_debitos, quantidade_lancamentos, saldo_final, ultima_atualizacao |
| `eventos-processados` | `EVENTO#<evento_id>` | — | processado_em, ttl |

**Consultas que essa chave habilita, sem nenhum índice secundário:**

| Necessidade | Operação |
|---|---|
| Lançamentos do comerciante em um período | `Query` com `begins_with` / `between` na SK |
| Saldo de um dia | `GetItem` |
| Saldos de um mês | `Query` com `between` na SK |
| Verificar duplicata | `GetItem` na tabela de dedup |

Nenhum `scan`. Nenhum GSI. O isolamento entre comerciantes é garantido **pela
chave**, não por filtro de aplicação — o que também elimina a classe de bug em
que um `if` esquecido vaza dados entre clientes.
