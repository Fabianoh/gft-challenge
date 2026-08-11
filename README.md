# Sistema de Controle de Fluxo de Caixa

Solução serverless na AWS para um comerciante registrar lançamentos (débitos e
créditos) e consultar o saldo diário consolidado.

---

## Índice da documentação

O README é a porta de entrada. O detalhamento arquitetural está em `docs/`.

| Documento | Conteúdo |
|---|---|
| [Domínios e capacidades de negócio](docs/01-dominios-e-capacidades.md) | Mapa de capacidades, bounded contexts, context map, linguagem ubíqua |
| [Requisitos e NFRs](docs/02-requisitos-e-nfrs.md) | Requisitos refinados, SLIs/SLOs, premissas assumidas |
| [Arquitetura alvo — C4](docs/03-arquitetura-alvo.md) | Diagramas de contexto, contêiner e componente; fluxos; modelo de dados |
| [Arquitetura de transição](docs/04-arquitetura-transicao.md) | Migração de legado em 6 fases (Strangler Fig), riscos e cronograma |
| [Estimativa de custos](docs/05-estimativa-custos.md) | Dois cenários, otimizações, ponto de virada serverless × containers |
| [Observabilidade](docs/06-observabilidade.md) | SLIs, catálogo de alarmes justificados, métricas de negócio, runbooks |
| [Segurança](docs/07-seguranca.md) | Modelo de ameaças, autenticação/autorização, critérios de integração |
| [ADR-001 — Serverless e granularidade](docs/adr/0001-serverless-e-granularidade-dos-servicos.md) | Por que dois serviços; por que Lambda e não containers |
| [ADR-002 — DynamoDB vs. relacional](docs/adr/0002-dynamodb-vs-relacional.md) | Escolha do banco e crítica à modelagem atual |
| [ADR-003 — Comunicação assíncrona](docs/adr/0003-comunicacao-assincrona.md) | SQS vs. SNS vs. Kinesis; idempotência; outbox |
| [ADR-004 — Agregação incremental](docs/adr/0004-agregacao-incremental-vs-recalculo.md) | Por que o desenho atual não atende 50 rps e como corrigir |
| [ADR-005 — Consistência eventual](docs/adr/0005-consistencia-eventual.md) | O trade-off imposto pelo requisito de isolamento |
| [Estratégia de testes](tests/README.md) | Camadas, cobertura, lacunas conhecidas, teste de carga |
| [Resultado do teste de carga](tests/carga/RESULTADO.md) | Medição do NFR de 50 rps contra a stack implantada |
| [Documentação da API](api_documentation.md) | Endpoints, payloads, códigos de retorno |

---

## Como rodar localmente

Sem conta AWS, sem credencial, sem Docker. Só Python 3.10+.

```bash
python tarefas.py instalar
```

```bash
python tarefas.py subir
```

```bash
python tarefas.py rodar
```

> Em Linux e macOS, `make install`, `make up` e `make run` fazem o mesmo.
> `tarefas.py` existe porque o `make` não vem instalado no Windows, e exigir que
> o avaliador instale um utilitário só para rodar o projeto seria atrito
> desnecessário. Os dois caminhos são equivalentes.

A API sobe em `http://localhost:3000`. Em outro terminal:

```bash
curl -X POST http://localhost:3000/lancamentos -H 'Content-Type: application/json' -d '{"tipo":"CREDITO","valor":"1500.50","descricao":"Venda balcao"}'
```

```bash
curl "http://localhost:3000/consolidado?data=$(date +%F)"
```

O saldo aparece alguns segundos depois do lançamento — essa defasagem é a
consistência eventual em ação, e é intencional ([ADR-005](docs/adr/0005-consistencia-eventual.md)).

Para encerrar:

```bash
python tarefas.py derrubar
```

### O que roda no ambiente local

| Componente na AWS | Substituto local |
|---|---|
| API Gateway | Roteador HTTP em `scripts/api_local.py`, que traduz a requisição no mesmo evento proxy (`httpMethod`, `resource`, `pathParameters`) |
| Lambda | Um processo Python por serviço, invocando o mesmo `lambda_handler` sem alteração |
| Lambda Authorizer | **Ausente localmente.** O roteador não valida token, para não exigir credencial AWS no desenvolvimento. A API implantada exige |
| Event Source Mapping | Thread que faz long polling na fila e entrega o evento no formato `{"Records": [...]}` |
| DynamoDB, SQS, S3, EventBridge | Emulados em memória (`moto`) |
| ElastiCache Redis | Ausente. O código já trata cache indisponível caindo para o banco |

Alternativa com emuladores reais em contêiner (DynamoDB Local + ElasticMQ):

```bash
make up-docker
```

### Demais comandos

```bash
python tarefas.py
```

| Comando | Equivalente com make | O que faz |
|---|---|---|
| `python tarefas.py testar` | `make test` | Roda as três suítes (128 testes) |
| `python tarefas.py cobertura` | `make cov` | Roda com relatório de cobertura |
| `python tarefas.py lacunas` | `make lacunas` | Lista as lacunas conhecidas do MVP |
| `python tarefas.py carga` | `make carga API_URL=...` | Teste de carga de 50 rps (requer k6) |
| `python tarefas.py empacotar` | `make package` | Empacota as Lambdas a partir do fonte |

---

## Arquitetura

### Domínios

O sistema tem **dois bounded contexts**, separados por terem requisitos de
disponibilidade **opostos** — e não por conveniência técnica:

| | **Lançamentos** | **Consolidação** |
|---|---|---|
| Responsabilidade | Registrar o fato financeiro | Apurar a posição diária |
| Natureza do dado | Fonte da verdade, imutável | Derivado, reconstruível |
| Consistência | Forte | Eventual (p95 < 30 s) |
| Disponibilidade alvo | 99,9% | 99,5% |
| Se cair | Venda perdida, irrecuperável | Nada se perde; recalcula |

Essa diferença é o que justifica o requisito não-funcional do enunciado — *"o
serviço de lançamento não deve ficar indisponível se o consolidado cair"* — e é
de onde decorre toda a arquitetura. Detalhamento em
[domínios e capacidades](docs/01-dominios-e-capacidades.md).

### Fluxo

![Arquitetura do sistema de controle de fluxo de caixa](docs/diagramas/arquitetura-fluxo-de-caixa.drawio.svg)

> A imagem é um **SVG editável**: o XML do draw.io está embutido nela, então o
> mesmo arquivo renderiza aqui e abre no [diagrams.net](https://app.diagrams.net)
> para edição. Sem duas cópias para manter em sincronia.

O acoplamento entre os dois é **apenas a fila**. Consolidação nunca chama
Lançamentos.

### Justificativa das escolhas

Cada decisão está registrada como ADR, com **alternativas consideradas e
descartadas** — não apenas com a lista de vantagens da opção escolhida:

| Decisão | Alternativa principal descartada | Por quê |
|---|---|---|
| Dois serviços serverless | Monólito em Fargate | Não atenderia o isolamento de falhas exigido |
| DynamoDB | Aurora Serverless v2 | Custo de piso 24×7 e atrito de pool de conexões com Lambda |
| SQS Standard | SQS FIFO / Kinesis | Ordem é desnecessária (soma é comutativa); FIFO limitaria a 300 msg/s por grupo |
| Consistência eventual | Escrita síncrona nas duas tabelas | Recriaria o acoplamento que o requisito proíbe |

---

## Estado da implementação

Esta seção separa o que **está implantado** do que é **arquitetura alvo**.
Componentes de desenho aparecem nos diagramas mas não estão no MVP.

### Implementado

| Componente | Papel |
|---|---|
| API Gateway (REST) | Entrada única, validação de payload, throttling, WAF |
| Lambda Lançamentos | Registro, validação, publicação de evento |
| Lambda Consolidado | Consumo do evento, apuração, consultas |
| DynamoDB (2 tabelas) | Persistência, com KMS e PITR |
| SQS + DLQ | Desacoplamento entre os domínios |
| ElastiCache Redis | Cache de leitura, Multi-AZ |
| S3 | Arquivamento de relatórios |
| CloudWatch + X-Ray | Métricas, alarmes, dashboard, tracing |
| WAF | Proteção de borda e rate limiting |
| **Lambda Authorizer** | **Autenticação JWT e autorização por escopo em todos os endpoints de negócio** |
| CloudFormation | Infra como código, em stacks aninhadas |

### Arquitetura alvo, ainda não implementada

| Componente | Motivo de estar no alvo |
|---|---|
| CloudFront + S3 estático | Distribuição do frontend com cache de borda |
| DynamoDB Streams → EventBridge Pipes | Outbox transacional ([ADR-003](docs/adr/0003-comunicacao-assincrona.md)) |
| Job de reconciliação | Detectar divergência entre lançamentos e saldo |
| Pipeline de CI/CD | Lint, testes, análise estática de IaC, deploy |

### Limitações conhecidas

Estão documentadas, priorizadas e **cobertas por teste** — não são surpresas:

| # | Limitação | Impacto |
|---|---|---|
| 1 | Apuração recalcula o dia por varredura de tabela | **Não atende os 50 rps do enunciado** ([ADR-004](docs/adr/0004-agregacao-incremental-vs-recalculo.md)) |
| 2 | Consolidado lê a base de Lançamentos | Acoplamento de dados; ameaça o isolamento exigido |
| 3 | Escrita dupla banco+fila sem transação | Evento pode se perder silenciosamente ([ADR-003](docs/adr/0003-comunicacao-assincrona.md)) |
| 4 | Dia de negócio apurado em UTC | Venda após 21h (BRT) cai no dia seguinte do relatório |
| 5 | Dinheiro serializado como `float` na resposta | Erro acumula em somatórios |
| 6 | Sem `comerciante_id` no modelo | Sistema funciona para um único comerciante |
| 7 | Sem CI/CD | Deploy manual |
| 8 | Ambiente local roda sem autenticação | O roteador local não invoca o authorizer; a API implantada exige token |

```bash
python tarefas.py lacunas
```

---

## Evoluções futuras

**Curto prazo (fecha as lacunas críticas)**
- Autenticação: OAuth 2.0 *client credentials* para integração servidor-a-servidor,
  Cognito/OIDC para acesso humano, escopos por endpoint (`lancamentos:write`,
  `consolidado:read`), mTLS para parceiros
- Agregação incremental com contador atômico + deduplicação transacional
- Chave composta com `comerciante_id` (multi-tenant por construção)
- Roles separadas por função; remoção de `ec2:DeleteSecurityGroup` da política
- Alarmes de DLQ e de defasagem do saldo; retenção de log
- Pipeline de CI/CD com `cfn-lint` e `checkov`

**Médio prazo**
- Estorno de lançamento como evento de compensação (prática contábil correta)
- Job diário de reconciliação, publicando a divergência como métrica de negócio
- Camada analítica: arquivamento no S3 + Athena, separando o caminho analítico do
  transacional
- Projeções adicionais a partir do mesmo fluxo de eventos: saldo por categoria,
  por meio de pagamento, projeção de fluxo futuro

**Longo prazo**
- Registro como *event sourcing* pleno, com o saldo virando uma entre várias
  projeções
- Multi-região ativo-passivo, se o RTO exigido justificar o custo
- Antecipação de recebíveis e conciliação bancária como novos bounded contexts,
  consumindo o mesmo barramento de eventos sem alterar o produtor

---

## Implantação na AWS

> **Não é necessário implantar para avaliar o projeto.** A suíte de 139 testes e
> o ambiente local completo rodam sem conta AWS — veja
> [Como rodar localmente](#como-rodar-localmente). Esta seção é para quem quiser
> ver o sistema no ar.

### Antes de começar

**Pré-requisitos:**

| | |
|---|---|
| Repositório clonado | `git clone https://github.com/Fabianoh/gft-challenge.git` |
| Python | 3.10 ou superior |
| AWS CLI | v2, configurado com `aws configure` |
| Região | Definida no perfil, ou passada com `--region` em cada comando |
| Permissões | Amplas — a stack cria IAM roles nomeadas, VPC, NAT, ElastiCache, WAF, entre outros |

**Confirme que o CLI está pronto:**

```bash
aws sts get-caller-identity
```

```bash
aws configure get region
```

Se o segundo comando não devolver nada, defina a região — sem ela **todo comando
`aws` abaixo falha** com *"You must specify a region"*:

```bash
aws configure set region us-east-1
```

**Sobre as permissões.** A stack provisiona recursos de doze serviços diferentes
e cria IAM roles com nome fixo, o que exige `CAPABILITY_NAMED_IAM`. Na prática é
preciso um usuário com permissões administrativas, ou uma política que cubra
CloudFormation, IAM, EC2/VPC, Lambda, API Gateway, DynamoDB, S3, SQS, SNS,
ElastiCache, KMS, Secrets Manager, CloudWatch e WAFv2. Um usuário com permissões
parciais falha no meio da criação e dispara rollback.

**Custo.** A stack provisiona recursos que cobram por hora, rodando 24/7,
independentemente de haver tráfego:

| Recurso | Custo mensal aproximado |
|---|---|
| ElastiCache Redis (2 nós `cache.t3.micro`) | US$ 25 |
| NAT Gateway | US$ 33 |
| WAF | US$ 8 |
| KMS + Secrets Manager | US$ 2 |
| **Total fixo** | **≈ US$ 68/mês** |

Um dia de testes custa cerca de US$ 2. **Destrua a stack ao terminar** — o
comando está no fim desta seção. Esse custo fixo é justamente o ponto levantado
em [estimativa de custos](docs/05-estimativa-custos.md): numa arquitetura
"serverless", quase toda a conta em baixo volume vem de componentes que rodam
o tempo todo.

**Tempo:** cerca de 20 minutos. São 12 stacks aninhadas criadas em sequência, e
o ElastiCache sozinho leva uns 10 minutos.

---

### 1. Empacote as funções e as camadas

```bash
python tarefas.py empacotar
```

Gera cinco `.zip` em `infra/lambdas/`. **Este passo é obrigatório:** os pacotes
não são versionados, porque binário no repositório impede build reproduzível —
não há garantia de que o `.zip` corresponda ao código que está no git.

### 2. Crie o bucket de artefatos

O CloudFormation não lê arquivos do seu disco: ele busca os templates aninhados
e os pacotes das Lambdas por HTTP no S3.

> Escolha um nome **só com letras minúsculas** e **único no mundo** — o espaço de
> nomes do S3 é global. Ex.: `gft-challenge-deploy-seunome`.

```bash
aws s3 mb s3://SEU-BUCKET
```

```bash
aws s3 sync . s3://SEU-BUCKET --exclude ".venv/*" --exclude ".git/*" --exclude "dist/*"
```

Este bucket é só de implantação. Não confundir com o bucket que a própria stack
cria para arquivar relatórios.

### 3. Crie a stack

Substitua `SEU-BUCKET` e `voce@exemplo.com`:

```bash
aws cloudformation create-stack --stack-name gft-challenge --template-url https://s3.amazonaws.com/SEU-BUCKET/infra/cfn/main.yml --parameters ParameterKey=AlertEmail,ParameterValue=voce@exemplo.com ParameterKey=BucketCFN,ParameterValue=SEU-BUCKET --capabilities CAPABILITY_NAMED_IAM
```

| Parâmetro | Para que serve |
|---|---|
| `AlertEmail` | Recebe os alarmes do CloudWatch — DLQ, defasagem do saldo, throttling. Ver aviso abaixo |
| `BucketCFN` | O bucket do passo 2, de onde o CloudFormation busca templates e pacotes |

> ### ⚠️ Você vai receber um e-mail da AWS
>
> Poucos minutos após criar a stack, o endereço informado em `AlertEmail` recebe
> uma mensagem com assunto **"AWS Notification — Subscription Confirmation"**.
>
> **É preciso clicar em "Confirm subscription".** Sem isso a inscrição fica
> pendente: os alarmes continuam disparando, mas não notificam ninguém.
>
> Não é spam nem cobrança — é o SNS confirmando que o endereço aceita receber os
> alertas. Se preferir não confirmar, tudo funciona igual; você só não recebe as
> notificações.

### 4. Acompanhe a criação

```bash
aws cloudformation describe-stacks --stack-name gft-challenge --query "Stacks[0].StackStatus" --output text
```

Repita até aparecer `CREATE_COMPLETE`. Se aparecer `ROLLBACK_*` ou
`CREATE_FAILED`, este comando mostra a causa:

```bash
aws cloudformation describe-stack-events --stack-name gft-challenge --query "StackEvents[?ResourceStatus=='CREATE_FAILED'].[LogicalResourceId,ResourceStatusReason]" --output table
```

### 5. Obtenha a URL da API

```bash
aws cloudformation describe-stacks --stack-name gft-challenge --query "Stacks[0].Outputs" --output table
```

### 6. Gere um token e chame a API

A API exige autenticação — sem token, todo endpoint de negócio responde **401**.

```bash
python scripts/gerar_token.py
```

O script lê o segredo de assinatura do Secrets Manager (criado pela stack) e
imprime um token válido por uma hora. Use-o no cabeçalho:

```bash
curl -H "Authorization: Bearer SEU-TOKEN" "https://SEU-ID.execute-api.us-east-1.amazonaws.com/dev/consolidado"
```

Para ver a **autorização por escopo** em ação, gere um token restrito. Ele
registra lançamento, mas recebe **403** ao tentar ler o saldo — é o que impede
que um PDV comprometido exponha a posição financeira do comerciante:

```bash
python scripts/gerar_token.py --escopos lancamentos:write
```

### 7. Destrua a stack ao terminar

```bash
aws cloudformation delete-stack --stack-name gft-challenge
```

O bucket de artefatos precisa ser esvaziado e removido à parte:

```bash
aws s3 rb s3://SEU-BUCKET --force
```

> O bucket de relatórios criado pela stack tem versionamento habilitado e pode
> impedir a exclusão se contiver objetos. Nesse caso, esvazie-o pelo console e
> repita o `delete-stack`.

---

### Resolução de problemas

| Sintoma | Causa provável |
|---|---|
| `Error occurred while GetObject` nas Lambdas | O passo 1 não foi executado, ou o `BucketCFN` não é o mesmo bucket do `sync` |
| `S3 error: Access Denied` ao criar a stack | Os templates não foram enviados, ou o nome do bucket está errado |
| Nenhum alarme chega por e-mail | A inscrição do SNS não foi confirmada (ver passo 3) |
| Todo endpoint responde 401 | Falta o cabeçalho `Authorization`, ou o token expirou (validade de 1 hora) |
| 403 num endpoint específico | O token não tem o escopo exigido — é o comportamento esperado |
| A stack demora mais de 25 min | Normal se o ElastiCache ainda estiver criando; confira o status pelo passo 4 |

---

## Estrutura do repositório

```
apps/
  lancamentos/     código do serviço de registro
  consolidado/     código do serviço de apuração
docs/
  adr/             decisões arquiteturais (contexto, alternativas, consequências)
  diagramas/       fonte editável em draw.io (a visão canônica é o Mermaid em 03-)
  01-dominios-e-capacidades.md
  02-requisitos-e-nfrs.md
infra/cfn/         infraestrutura como código (CloudFormation)
scripts/           execução local e empacotamento
tests/
  lancamentos/     suíte do serviço de registro
  consolidado/     suíte do serviço de apuração
  carga/           teste de carga k6 (NFR de 50 rps)
```
