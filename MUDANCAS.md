# O que mudou e por quê

Guia de leitura das alterações feitas no projeto. Cada item traz **o que mudou**,
**por que mudou** e **como você defende isso na entrevista**.

Duas etapas de trabalho:

1. **P0** — fechou os requisitos obrigatórios: testes, execução local, documentação
2. **P1 de infraestrutura** — autenticação e isolamento entre os domínios

Todos os templates passam no `cfn-lint` sem erro. **A stack precisa ser implantada
uma vez antes da entrega** — as alterações de infra foram validadas por análise
estática, não por deploy real.

---

## Em uma frase

O projeto tinha três requisitos **obrigatórios** não atendidos (testes,
execução local, documentação de domínios/requisitos), e o enunciado avisa que
isso causa descarte. Os três foram fechados. No caminho, a suíte de testes
encontrou quatro bugs reais de runtime, que também foram corrigidos.

---

## 1. Testes — de placeholder a 94 testes

### O que existia

`apps/lancamentos/testes.py`, 8 linhas:

```python
from main import lambda_handler
context = ''
event = {'Payload': 'Insira o Payload a ser recebido pelo lambda'}
lambda_handler(event, context)
```

Sem framework, sem asserção, sem execução. Não é teste — é rascunho.

### O que passou a existir

| Arquivo | Testes | Cobre |
|---|---|---|
| `tests/lancamentos/test_validacao.py` | 27 | Regras de negócio da validação |
| `tests/lancamentos/test_api_lancamentos.py` | 23 | Handler completo contra AWS simulada |
| `tests/lancamentos/test_lacunas_conhecidas.py` | 3 | Lacunas do MVP (P1) |
| `tests/consolidado/test_calculo_saldo.py` | 17 | Cálculo do saldo diário |
| `tests/consolidado/test_consumo_sqs.py` | 8 | Consumo assíncrono e falhas |
| `tests/consolidado/test_api_consolidado.py` | 19 | Endpoints de consulta |
| `tests/consolidado/test_lacunas_conhecidas.py` | 3 | Lacunas do MVP (P1) |

**94 passando + 6 lacunas documentadas.** Cobertura: 83% (lançamentos),
74% (consolidado). Nenhum precisa de conta AWS — tudo simulado em memória com
`moto`.

### Por que rodam em dois processos separados

`tarefas.py testar` chama o pytest duas vezes. Isso é deliberado: os dois serviços têm
arquivos de mesmo nome (`main.py`, `utils.py`, `operacoes.py`, `sqs.py`,
`configuration.py`), porque cada um é empacotado com seus módulos na raiz do
`.zip` do Lambda. Carregá-los no mesmo processo faria um sobrescrever o outro em
`sys.modules` — uma colisão que **não existe em produção**. Processos separados
reproduzem o isolamento real. `tests/conftest.py` detecta a tentativa de rodar
juntos e explica.

> **Na entrevista:** essa é uma boa resposta para "como você testa
> microsserviços?". A resposta não é "tudo num pytest só" — é reconhecer que
> unidades de deploy independentes se testam independentemente.

### O detalhe que mais impressiona: as lacunas viraram teste

6 testes descrevem comportamento que a arquitetura alvo exige e o MVP **ainda não
entrega**. Eles usam `xfail(strict=True)`:

- enquanto o defeito existe → **XFAIL**, não quebra o build
- quando a correção entrar → passa, e o pytest acusa **XPASS como FALHA**,
  obrigando a remover o marcador

> **Na entrevista:** *"O roadmap não está num quadro que envelhece — está no
> código, executável. Quando a correção entra, o teste me obriga a atualizar o
> status."* Isso demonstra rigor de engenharia, e transforma "não deu tempo" em
> "está mapeado e verificável".

```bash
python tarefas.py lacunas
```

---

## 2. Bugs reais que os testes encontraram

Não foram procurados — apareceram ao escrever a suíte. Todos corrigidos, todos
com teste de regressão.

### 2.1 `ValidationError` nunca foi definida (o mais grave)

`apps/lancamentos/utils.py` levantava `ValidationError` em **6 pontos**, mas a
classe **não existia nem era importada**. Toda validação estourava `NameError`,
capturado por um `except Exception` genérico e devolvido ao cliente como:

```json
{"error": "Dados inválidos", "message": "name 'ValidationError' is not defined"}
```

**Nenhuma mensagem de validação do sistema funcionava**, e um erro interno vazava
para o cliente.

**Correção:** criado `apps/lancamentos/excecoes.py` com hierarquia de exceções de
domínio (`LancamentoError` → `ValidationError`, `PersistenciaError`). O
`except Exception` virou `except ValidationError`, para que um erro inesperado
seja 500 (falha do servidor) e não 400 (culpa do cliente).

### 2.2 `raise "string"` em 8 lugares

```python
raise "Erro ao salvar lançamento"          # dynamodb.py
raise "Erro ao gerar relatório consolidado" # operacoes.py
raise f"Erro no processamento: {e}"         # sqs.py
```

Python exige uma instância de exceção. Isso levanta
`TypeError: exceptions must derive from BaseException`, **descartando a causa
raiz** exatamente no momento em que ela é necessária para o diagnóstico.

**Correção:** exceções tipadas com `raise ... from e`, preservando o
encadeamento.

### 2.3 `{"valor": "abc"}` virava HTTP 500

`Decimal("abc")` levanta `InvalidOperation`, que herda de `ArithmeticError` — não
de `ValueError`. O `except (ValueError, TypeError)` não a capturava. Resultado:
erro do cliente virava falha do servidor, **disparando alarme indevidamente**.

**Correção:** `InvalidOperation` incluída no tratamento; agora retorna 400.

### 2.4 `{"valor": 0}` devolvia a mensagem errada

A checagem de campo obrigatório usava veracidade (`not data[field]`), e `0` é
falso em Python. Um lançamento de valor zero recebia *"Campo 'valor' é
obrigatório"* quando o correto é *"Valor deve ser maior que zero"*.

**Correção:** checagem por ausência real (`is None` / coleção vazia).

### 2.5 `except BaseException` em 24 lugares

`BaseException` captura também `KeyboardInterrupt` e `SystemExit`. Todos viraram
`except Exception`.

> **Na entrevista:** *"Eu não fui procurar bugs — escrevi os testes e eles
> apareceram. É o argumento mais direto de por que teste automatizado paga o
> investimento: quatro defeitos que passariam para produção, sendo que um
> quebrava 100% do caminho de validação."*

---

## 3. Execução local — requisito obrigatório, antes impossível

### O que existia

O README ensinava a criar bucket, subir templates e rodar `create-stack`. Isso é
implantação na AWS, não execução local. Sem conta AWS, não havia como rodar nada.
Não havia nem `requirements.txt`.

### O que passou a existir

```bash
python tarefas.py instalar && python tarefas.py subir && python tarefas.py rodar
```

A API sobe em `http://localhost:3000`. **Sem conta AWS, sem credencial, sem
Docker.**

| Componente na AWS | Substituto local |
|---|---|
| API Gateway | Roteador em `scripts/api_local.py` que traduz HTTP no mesmo evento proxy |
| Lambda | Um processo por serviço, invocando o `lambda_handler` **sem alteração** |
| Event Source Mapping | Thread com long polling na fila |
| DynamoDB / SQS / S3 / EventBridge | Emulados (`moto`) |

Também há `make up-docker` (DynamoDB Local + ElasticMQ) para quem preferir
emuladores reais.

### O ponto que vale explicar

**O código da aplicação não foi alterado para rodar local.** O redirecionamento
usa as variáveis `AWS_ENDPOINT_URL*`, suportadas nativamente pelo botocore. O
mesmo `lambda_handler` roda idêntico nos dois ambientes — o que preserva a
validade do teste local.

### Um bug encontrado ao validar

Durante o teste do fluxo local, um POST com acento (`"Venda balcão"`) derrubava a
conexão: o `curl` do terminal Windows envia o corpo no code page local, e o
`decode("utf-8")` estrito estourava `UnicodeDecodeError` **dentro do handler**,
fechando o socket sem resposta. O cliente via "connection reset", que não diz
nada sobre a causa.

Corrigido em `decodificar_corpo()`: tenta UTF-8, cai para latin-1 (que aceita
qualquer byte). Assim um payload mal codificado vira erro de negócio, não queda
de conexão.

Também descoberto e corrigido: as chamadas ao **EventBridge estavam escapando
para a AWS real** (`UnrecognizedClientException` nos logs). Um ambiente local
nunca pode tocar a AWS de verdade — resolvido com `AWS_ENDPOINT_URL` curinga.

---

## 4. Documentação arquitetural — o que mais pesa na sua nota

Para uma vaga de **Arquiteto de Soluções**, esta é a parte de maior peso. O
enunciado é explícito: *"não é necessário que todas essas premissas sejam
apresentadas na codificação, mas nas decisões e representações arquiteturais"*.

### 4.1 `docs/01-dominios-e-capacidades.md` — requisito obrigatório, antes ausente

Antes: dois bullets no README chamados "domínios funcionais".

Agora: mapa de 8 capacidades classificadas em **core / supporting / generic**,
dois bounded contexts detalhados, **context map** com o padrão de relacionamento
nomeado (Publisher/Subscriber com Anticorruption Layer), e linguagem ubíqua.

**O argumento central do documento**, que vale memorizar:

> Registrar um fato e interpretá-lo são capacidades com criticidade **oposta**.
> Se o registro falhar, a venda se perde e não há como recuperá-la — o fato
> aconteceu no mundo e não foi capturado. Se a apuração falhar, nada se perde:
> basta recalcular. **É daí que sai o requisito não-funcional do enunciado** — ele
> não é um detalhe de infraestrutura, é consequência da diferença entre as duas
> capacidades.

> **Na entrevista:** comece por aqui, nunca por "usei Lambda e DynamoDB". Quem
> começa pela tecnologia se apresenta como desenvolvedor; quem começa pela
> capacidade de negócio se apresenta como arquiteto.

### 4.2 `docs/02-requisitos-e-nfrs.md` — requisito obrigatório, antes ausente

18 requisitos funcionais rastreados **ao teste que os verifica**, os dois NFRs do
enunciado refinados em condições verificáveis, tabela de **SLIs/SLOs com
justificativa de negócio**, e as premissas assumidas.

Dois destaques:

**O RNF-01 refinado em cinco condições.** Dizer "usei SQS" é insuficiente: a fila
resolve o acoplamento *temporal*, mas sobram quatro recursos compartilhados que
reintroduzem o acoplamento (concorrência Lambda, cota do API Gateway, base de
dados, role IAM). Três dessas condições estão **em aberto** e assim declaradas.

**A premissa P6, que vale levantar sozinho na conversa:**

> O enunciado pede, ao mesmo tempo, que os serviços sejam independentes e que
> exista saldo consolidado. Isso só é conciliável com consistência eventual — não
> existe desenho que entregue isolamento total **e** saldo síncrono. Explicitar
> esse trade-off é responsabilidade do arquiteto.

### 4.3 `docs/adr/` — 5 ADRs

O requisito *"justificativa na decisão/escolha de ferramentas/tecnologias"* é
literalmente isso. O README antigo listava só **vantagens** de cada serviço AWS —
o que não é justificativa, é catálogo de marketing. Justificativa exige
**alternativa descartada e o motivo**.

| ADR | Decisão | Alternativa descartada |
|---|---|---|
| 001 | Dois serviços serverless | Monólito em Fargate; EKS; Step Functions |
| 002 | DynamoDB | Aurora Serverless v2; DocumentDB; Timestream |
| 003 | SQS Standard + EventBridge | SQS FIFO; SNS; Kinesis |
| 004 | Agregação incremental | Trocar scan por query; lote; job noturno; Flink |
| 005 | Consistência eventual | 2PC; escrita síncrona nas duas tabelas |

Todos seguem: contexto → decisão → **alternativas consideradas** → consequências
(positivas **e** negativas) → ponto de reavaliação.

---

## 5. Teste de carga — o NFR numérico do enunciado

`tests/carga/consolidado-50rps.js` (k6) verifica:

> *"Em dias de picos, o serviço de consolidado diário recebe 50 requisições por
> segundo, com no máximo 5% de perda."*

Este é o **único requisito do enunciado com meta numérica**, então é verificado
com número.

Dois detalhes que valem explicar:

**`constant-arrival-rate`, não usuários virtuais.** Um cenário baseado em VUs
reduz a carga sozinho quando o serviço fica lento, escondendo justamente a
degradação que se quer medir — é o *coordinated omission*. A taxa de chegada
precisa ser fixa.

**Os limiares são porta de qualidade.** Se estourarem, o k6 sai com código 1 e o
pipeline falha. O NFR vira verificação executável, não parágrafo de documento.

> ⚠️ **Este teste ainda não foi executado contra ambiente implantado** — exige a
> stack no ar. Pela análise de custo por evento do ADR-004, a expectativa é que o
> desenho atual **não** atinja a meta.

---

## 6. Higiene do repositório

| Item | Antes | Agora |
|---|---|---|
| Dependências | Não declaradas | `requirements.txt` + `requirements-dev.txt` |
| Comandos | Nenhum | `Makefile` com `help` |
| Pacotes Lambda | `.zip` **versionados no git** | `tarefas.py empacotar` gera a partir do fonte; `.gitignore` exclui |
| Blocos de código no README | `'''` (não renderizam no GitHub) | crases corretas |
| Erros de digitação | "PERMISSAS", "Automento", "rololback" | corrigidos |
| Alvo vs. implementado | Misturados | Seções separadas + tabela de 10 limitações conhecidas |

**Sobre o `.zip` versionado:** versionar binário impede build reproduzível — não
há garantia de que o pacote corresponda ao código no repositório. O empacotador
gera com timestamp fixo e ordem estável, de modo que o mesmo fonte produz sempre
o mesmo hash.

---

## 7. O que NÃO foi alterado, e por quê

**Toda a infraestrutura (`infra/cfn/**`) está intacta.** As correções de infra
são P1/P2 e mudam comportamento de deploy — mexer nelas sem poder validar contra
uma conta AWS seria arriscado. Elas estão analisadas e priorizadas em
`ANALISE-E-PLANO.md`:

- `AuthorizationType: NONE` em todos os 11 métodos (**crítico**)
- Sem reserved concurrency (modo de falha mais provável do RNF-01)
- Throttle único de 100 rps no stage, compartilhado pelos dois serviços
- Role IAM única, com `ec2:DeleteSecurityGroup` em `*`
- Sem alarme de DLQ nem de defasagem
- `DestinationArn` do access log comentado
- `infra/cfn/desafio.yml`: 1.335 linhas 100% comentadas, não referenciadas

**A lógica de negócio também não foi reescrita.** As correções P1 (agregação
incremental, outbox via Streams, fuso do comerciante, Decimal na resposta,
idempotência) estão **especificadas nos ADRs e cobertas por teste**, mas não
implementadas. Isso é escopo consciente, não omissão — e a forma como está
documentado é defensável.

---

## 8. Roteiro de 3 minutos para a entrevista

Se você tiver pouco tempo para apresentar, use esta ordem:

**1. Comece pelo negócio (30s).**
> "Identifiquei duas capacidades core com criticidade oposta: registrar o fato
> financeiro e apurar a posição. Registro perdido é receita irrecuperável;
> apuração perdida se recalcula. Essa diferença é a razão de existir do requisito
> não-funcional do enunciado, e é dela que decorre toda a arquitetura."

**2. Mostre a decisão e o trade-off (60s).**
> "Separei em dois bounded contexts com comunicação assíncrona por evento. O
> preço é consistência eventual no saldo — e eu declaro a janela: p95 abaixo de
> 30 segundos, medida como SLI, com `ultima_atualizacao` exposto na API para o
> cliente saber a idade do dado. O enunciado pede isolamento total e saldo
> consolidado; essas duas coisas só coexistem com consistência eventual."

**3. Antecipe a crítica (60s).** — *é aqui que você ganha a vaga*
> "Ao validar meu próprio desenho contra o requisito de 50 rps, descobri que ele
> não atende: o recálculo por varredura dispara mais de 60 scans por evento. A
> correção é agregação incremental com contador atômico, que leva de O(N) para
> O(1). O trade-off é que `ADD` não é idempotente, então exige deduplicação
> transacional com `TransactWriteItems`. Está no ADR-004 e coberto por teste."

**4. Feche com rigor (30s).**
> "As seis lacunas conhecidas estão como testes `xfail` estritos: quando a
> correção entrar, o teste me obriga a atualizar o status. E os testes
> encontraram quatro bugs reais no código, incluindo um que quebrava 100% do
> caminho de validação."

**A pergunta que mais derruba candidato é "que problemas você vê na sua própria
solução?".** Você tem a resposta pronta, documentada e testada — o que é bem
melhor do que não ter problema nenhum a apontar.

---

## 9. P1 de infraestrutura — autenticação e isolamento

### 9.1 A API deixou de ser pública

Era a lacuna mais grave do projeto: **os 11 métodos estavam com
`AuthorizationType: NONE`**. Qualquer pessoa com a URL registrava lançamentos e
lia o saldo do comerciante.

Foi implementado um **Lambda Authorizer** (`apps/authorizer/`, 33 testes) que
valida assinatura HMAC-SHA256, emissor, audiência, expiração e **o escopo exigido
pelo recurso**:

| Escopo | Concede |
|---|---|
| `lancamentos:write` | `POST /lancamentos` |
| `lancamentos:read` | `GET /lancamentos`, `GET /lancamentos/{id}` |
| `consolidado:read` | `GET /consolidado`, `GET /consolidado/relatorio` |
| `metricas:read` | `GET /metricas` |

Os `OPTIONS` e o `/health` continuam abertos — navegador não envia `Authorization`
no preflight de CORS, e exigir token ali quebraria o CORS inteiro.

**Decisões que valem defender:**

- **HS256 com segredo no Secrets Manager**, não RS256/JWKS. Mantém a stack
  autocontida (o avaliador não precisa provisionar um provedor de identidade) e
  passa a **usar** o segredo que antes era criado e nunca lido. A troca por RS256
  é localizada em `_verificar_assinatura`.
- **Authorizer do tipo REQUEST**, não TOKEN: a decisão depende do recurso alvo,
  não apenas do token.
- **Negação por padrão.** Recurso não mapeado, `alg` inesperado, erro interno —
  tudo resulta em `Deny`. Um endpoint novo que alguém esqueça de mapear fica
  inacessível, em vez de aberto.
- **A negação não revela o motivo** ao cliente. Detalhar por que o token falhou
  entrega ao atacante um oráculo para ajustar a próxima tentativa.

Os testes cobrem os ataques clássicos: `alg: none`, corpo adulterado, token
expirado, emissor e audiência trocados, escopo insuficiente.

> **Na entrevista:** o teste `test_pdv_com_escopo_de_escrita_nao_le_o_saldo`
> codifica a razão de existir da autorização por escopo — um PDV comprometido
> numa loja não pode expor a posição financeira do negócio inteiro.

Como a API implantada passou a exigir token, criei `scripts/gerar_token.py`. Sem
isso, avaliar o projeto viraria arqueologia.

### 9.2 As condições em aberto do RNF-01

O requisito não-funcional principal dependia de quatro controles além da fila.
Três foram implementados:

| Controle | O que impedia |
|---|---|
| `ReservedConcurrentExecutions: 100` no lançamentos | Consolidado em retry-storm drenar a cota da conta e deixar o registro sem execuções |
| `ScalingConfig.MaximumConcurrency: 50` no consumidor | O mesmo, pelo outro lado: impõe teto ao consolidado |
| `MethodSettings` por recurso — 200 rps em lançamentos, 80 no consolidado | Um limite único de 100 rps no stage inteiro, em que tráfego de leitura consumia a cota de escrita |

A quarta — consolidado não competir por capacidade na tabela de lançamentos —
continua aberta e depende do ADR-004.

### 9.3 `ReportBatchItemFailures`: infra e código juntos

Habilitar a flag no event source mapping **sem mudar o código não teria efeito**.
Foram alterados os dois lados.

`handle_sqs_event` deixou de relançar a exceção no primeiro erro — o que derrubava
o lote inteiro e reprocessava mensagens já aplicadas — e passa a devolver os
identificadores que falharam. `main.lambda_handler` responde
`{"batchItemFailures": [...]}`.

Com agregação incremental (ADR-004), o comportamento antigo significaria
**contabilizar valores em dobro**.

### 9.4 Bugs de infraestrutura encontrados

Ler os templates com o requisito na mão revelou quatro defeitos que faziam a
observabilidade **parecer** configurada sem funcionar:

1. **Todos os alarmes estavam quebrados.** A dimensão `FunctionName` recebia o
   **ARN** da função, e o CloudWatch espera o **nome**. Os alarmes ficariam em
   `INSUFFICIENT_DATA` para sempre. Mesmo problema na dimensão `QueueName`.
2. **Nome de métrica inexistente.** O alarme de fila usava
   `ApproximateNumberOfVisibleMessages`; o correto é
   `ApproximateNumberOfMessagesVisible`. A métrica não existe — o alarme nunca
   dispararia.
3. **`/health` devolvia texto literal.** `${Environment}` estava dentro de um
   `ResponseTemplates` sem `Fn::Sub`, então a resposta continha a string
   literal em vez do nome do ambiente.
4. **`secrets.yml` com erro de copy-paste.** O campo `redis_port` recebia
   `${RedisEndpoint}`.

> **Na entrevista:** um alarme que nunca dispara é pior que nenhum alarme, porque
> cria falsa confiança. A observabilidade inteira parecia configurada e não teria
> funcionado no dia em que fosse necessária.

### 9.5 Demais correções

| Item | Antes | Agora |
|---|---|---|
| IAM | Uma role compartilhada pelas duas Lambdas | Três roles, uma por função |
| IAM | `ec2:CreateSecurityGroup` e `ec2:DeleteSecurityGroup` em `*` | Removidos — caminho de escalada desnecessário |
| IAM | `events:PutEvents` em `*` | Restrito ao barramento |
| WAF | Rate limit por IP, 1.000 / 5 min (~3,3 rps) | Por identidade, 20.000 / 5 min (~66 rps) |
| KMS | Sem rotação | `EnableKeyRotation` + janela de 30 dias |
| Logs | Retenção infinita | 30 dias, parametrizável |
| Access log | `DestinationArn` comentado | Log group dedicado |
| Alarmes | Sem DLQ, sem defasagem, sem throttle | Todos, mais um alarme composto |
| `desafio.yml` | 1.335 linhas comentadas, não referenciadas | Removido |

### 9.6 O conflito WAF × NFR

O achado que mais vale contar. A regra de rate limiting usava
`AggregateKeyType: IP` com `Limit: 1000`. No WAFv2 esse limite é **por janela de
5 minutos**, ou seja ~3,3 req/s por IP.

O enunciado exige **50 req/s**. São 15.000 requisições em 5 minutos — **15 vezes
o limite**. E o cenário é o comum, não o excepcional: uma rede de lojas sai por
um NAT corporativo e todo o tráfego chega com um IP só. **O WAF bloquearia
exatamente a carga que o requisito determina que o sistema precisa suportar.**

Nenhuma das duas configurações estava errada isoladamente. O erro foi não ter
havido quem olhasse as duas juntas.

> **Na entrevista:** é o melhor exemplo de visão sistêmica que o projeto oferece.
> Uma decisão de segurança e um requisito não-funcional, cada um correto por si,
> se anulavam. Encontrar isso é literalmente a função do arquiteto.

### 9.7 O que a mudança criou

Segurança não é uma lista de pendências que só encolhe. Fechar a autenticação
**elevou** a prioridade de outra ameaça: antes não havia token para vazar; agora
há, e `main.lambda_handler` continua registrando o evento inteiro no CloudWatch,
**incluindo o cabeçalho `Authorization`**. Virou o item mais urgente do roadmap
de segurança.

> Vale dizer isso em voz alta na entrevista: mudanças alteram o modelo de
> ameaças, não apenas o preenchem.

### 9.8 Validação e ressalva

- `cfn-lint`: **zero erros** em todos os templates (o baseline tinha 2)
- 128 testes passando: 50 lançamentos + 45 consolidado + 33 authorizer
- Empacotamento gera os três `.zip`

> **A stack não foi implantada.** As alterações de infra foram validadas por
> análise estática. **Suba a stack uma vez antes de entregar** — o `cfn-lint` não
> pega dependência circular em runtime nem problema de IAM que só aparece na
> criação.

---

## Próximos passos sugeridos

Na ordem de retorno sobre esforço:

1. **Implantar a stack** e confirmar que sobe. É a validação que falta.
2. **Rodar o teste de carga** contra a stack implantada e commitar o relatório.
   É o único requisito com meta numérica — demonstrá-lo com número é o maior
   diferencial possível.
3. **Parar de registrar o evento inteiro no log** (vazamento de token) — correção
   pequena, urgência criada pela autenticação.
4. **Agregação incremental** (ADR-004) — os testes `xfail` já estão prontos para
   virar verdes.
4. **Diagramas C4** (contexto → container → componente) + sequência do fluxo.
5. **Estimativa de custos** e **arquitetura de transição** — dois diferenciais do
   enunciado ainda ausentes.
6. **CI/CD** com GitHub Actions.
