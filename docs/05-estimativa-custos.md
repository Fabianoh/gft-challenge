# Estimativa de custos de infraestrutura e licenças

> Requisito **diferencial** do desafio: *"Estimativa de custos com infraestrutura
> e licenças"*.

> ⚠️ **Sobre os números:** preços de referência da região **us-east-1**, usados
> como **ordem de grandeza** para comparar decisões arquiteturais. Preço de nuvem
> muda; valide na calculadora oficial antes de qualquer decisão de orçamento. O
> que este documento entrega de duradouro não é o valor absoluto — é a **estrutura
> de custo** e onde ela dói.

---

## 1. A conclusão, antes das tabelas

Levantar os números expôs algo que não estava evidente no desenho:

> **No cenário de baixo volume, 99% da conta é custo fixo.** A solução é
> "serverless", mas paga cerca de **US$ 67/mês** para processar 10 mil
> lançamentos — dos quais **US$ 0,37** são consumo real e **US$ 67** são três
> componentes que rodam 24×7 independentemente de haver tráfego: ElastiCache, NAT
> Gateway e WAF.

Isso **contradiz a premissa que justificou a escolha por serverless** no
[ADR-001](adr/0001-serverless-e-granularidade-dos-servicos.md) — pagar pelo que se
usa. A arquitetura tem um piso de custo que a torna, em volume baixo, mais cara
que uma alternativa mais simples.

A boa notícia: os três componentes são removíveis sem prejuízo funcional, e a
remoção derruba a conta em **~85%**. Detalhe em §5.

---

## 2. Premissas de dimensionamento

| | Cenário A — pequeno comerciante | Cenário B — rede de lojas |
|---|---|---|
| Lançamentos/mês | 10.000 | 5.000.000 |
| Consultas ao consolidado/mês | 3.000 | 10.000.000 |
| Pico de leitura | ~2 req/s | **50 req/s** (do enunciado) |
| Requisições no API Gateway | 13.000 | 15.000.000 |
| Volume acumulado em 5 anos | ~600 MB | ~300 GB |
| Retenção | 5 anos (prazo fiscal brasileiro) | idem |

O Cenário B é dimensionado para o **desenho alvo** do
[ADR-004](adr/0004-agregacao-incremental-vs-recalculo.md) — agregação incremental.
Com o recálculo por varredura do MVP atual, o custo de DynamoDB seria ordens de
grandeza maior e o serviço nem completaria as requisições. Ver §6.

---

## 3. Cenário A — 10 mil lançamentos/mês

| Componente | Base de cálculo | Custo/mês | Tipo |
|---|---|---:|:---:|
| **ElastiCache Redis** | 2 × `cache.t3.micro` · 730 h | **US$ 24,82** | 🔴 fixo |
| **NAT Gateway** | 1 × 730 h · US$ 0,045/h | **US$ 32,85** | 🔴 fixo |
| **WAF** | 1 WebACL + 3 regras + requisições | **US$ 8,01** | 🔴 fixo |
| **KMS** | 1 chave gerenciada + requisições | **US$ 1,10** | 🔴 fixo |
| **Secrets Manager** | 1 segredo | **US$ 0,40** | 🔴 fixo |
| API Gateway REST | 13 mil × US$ 3,50/milhão | US$ 0,05 | 🟢 variável |
| Lambda | ~23 mil invocações · ~1.150 GB-s | US$ 0,02 | 🟢 variável |
| DynamoDB on-demand | escritas + leituras + 10 MB | US$ 0,06 | 🟢 variável |
| SQS | 10 mil mensagens | US$ 0,01 | 🟢 variável |
| CloudWatch Logs | ~46 MB de ingestão | US$ 0,02 | 🟢 variável |
| X-Ray | ~23 mil traces | US$ 0,11 | 🟢 variável |
| S3 + CloudFront | dentro do nível gratuito | US$ 0,00 | 🟢 variável |
| | | | |
| | **Total** | **≈ US$ 67,45** | |

```
Custo fixo (roda mesmo com zero tráfego) ..... US$ 67,18   ██████████████████ 99,6%
Custo variável (consumo real) ................ US$  0,27   ▏                   0,4%
```

**Custo por lançamento: US$ 0,0067** — cerca de 3,5 centavos de real. Para um
comerciante que registra 10 mil movimentos/mês, isso é caro pelo que entrega.

---

## 4. Cenário B — 5 milhões de lançamentos/mês

| Componente | Base de cálculo | Custo/mês | Tipo |
|---|---|---:|:---:|
| **API Gateway REST** | 15 mi × US$ 3,50/milhão | **US$ 52,50** | 🟢 variável |
| **CloudFront** | ~1,5 TB de transferência | **US$ 50,00** | 🟢 variável |
| **ElastiCache Redis** | 2 × `cache.t3.small` | **US$ 49,64** | 🔴 fixo |
| **NAT Gateway** | 730 h + processamento | **US$ 35,00** | 🔴 fixo |
| DynamoDB on-demand | 15 mi escritas + 5 mi leituras + 5 GB | US$ 21,25 | 🟢 variável |
| KMS | requisições de chave de dados | US$ 15,00 | 🟡 misto |
| WAF | base + 15 mi requisições | US$ 14,00 | 🟡 misto |
| Lambda | ~15,5 mi invocações · ~588 mil GB-s | US$ 12,89 | 🟢 variável |
| CloudWatch Logs | ~15,5 GB de ingestão | US$ 8,20 | 🟢 variável |
| X-Ray | 10% de amostragem · 1,55 mi traces | US$ 7,75 | 🟢 variável |
| SQS | ~11 mi requisições | US$ 4,40 | 🟢 variável |
| S3 | relatórios + arquivamento | US$ 2,00 | 🟢 variável |
| Secrets Manager | 1 segredo | US$ 0,40 | 🔴 fixo |
| | | | |
| | **Total** | **≈ US$ 273,03** | |

```
Custo fixo ................................... US$  85,04   ██████            31%
Custo variável ............................... US$ 188,00   █████████████     69%
```

**Custo por lançamento: US$ 0,0000546** — cerca de 0,03 centavo de real.

**Aqui a arquitetura brilha.** Um aumento de **500×** no volume produziu apenas
**4×** no custo. É exatamente o comportamento que se espera de um desenho
elástico, e é o argumento forte a favor da escolha serverless — desde que o
volume exista.

---

## 5. Otimizações: como cortar 85% do Cenário A

Cada item abaixo é uma decisão arquitetural, não um ajuste de configuração.

### 5.1 Remover o ElastiCache — economia de US$ 24,82/mês

Com a agregação incremental do [ADR-004](adr/0004-agregacao-incremental-vs-recalculo.md),
consultar o saldo de um dia é **um `GetItem`** — latência de ~5 ms.

O cache foi introduzido para amortecer o recálculo caro. Removida a causa,
some a necessidade. E ele é o componente que mais viola a premissa serverless:
roda 24×7 e cobra por hora, não por uso.

> **Um arquiteto que remove um componente e justifica a remoção demonstra mais
> do que um que adiciona.** Se o cache for mantido, é preciso responder com
> número: qual taxa de leitura justifica US$ 25/mês de custo fixo?

### 5.2 Tirar a Lambda de Lançamentos da VPC — economia de US$ 32,85/mês

Ela só acessa DynamoDB, SQS e EventBridge — **nenhum recurso de VPC**. Está lá
sem necessidade, e isso obriga a existir um NAT Gateway.

Ganhos além do custo: elimina a latência de ENI na partida a frio, e remove o NAT
como ponto único de falha (ele está numa AZ só).

Removido o ElastiCache (§5.1), **nenhuma função precisa mais de VPC** e o NAT
pode ser desprovisionado inteiro. Para os serviços fora da VPC, *gateway
endpoints* de S3 e DynamoDB são gratuitos.

### 5.3 API Gateway REST → HTTP API — economia de 71% no item

US$ 3,50/milhão → **US$ 1,00/milhão**. No Cenário B são US$ 52,50 → US$ 15,00,
uma economia de **US$ 37,50/mês**.

**Trade-off honesto:** o HTTP API não suporta associação direta com WAF, nem
*request validator*, nem *usage plans*. Para uma API financeira exposta à
internet, WAF tem valor real. A recomendação depende do modelo de exposição:

| Exposição | Recomendação |
|---|---|
| API pública na internet | Manter REST API com WAF, ou HTTP API atrás do CloudFront com WAF na distribuição |
| Consumo apenas interno / B2B com mTLS | HTTP API compensa |

### 5.4 Retenção de logs — economia crescente

Hoje não há `RetentionInDays` configurado: **os logs são retidos para sempre**.
O custo começa desprezível e cresce indefinidamente. Definir 30 dias para
operação e exportar o necessário para S3 (US$ 0,023/GB contra US$ 0,03/GB no
CloudWatch) resolve.

### 5.5 Amostragem no X-Ray

Traçar 100% das requisições em produção raramente se justifica. Amostragem de
5–10% preserva o valor diagnóstico e corta o custo proporcionalmente.

### Resultado consolidado

| | Antes | Depois | Economia |
|---|---:|---:|---:|
| **Cenário A** | US$ 67,45 | **US$ 9,78** | **85%** |
| **Cenário B** | US$ 273,03 | **US$ 160,72** | **41%** |

No Cenário A o custo por lançamento cai de US$ 0,0067 para **US$ 0,00098** — quase
sete vezes menos, sem perder nenhuma capacidade funcional.

---

## 6. O custo do defeito atual

Vale dimensionar o que o problema do [ADR-004](adr/0004-agregacao-incremental-vs-recalculo.md)
custaria em dinheiro, porque isso costuma convencer mais rápido que o argumento
técnico.

No MVP, **cada lançamento dispara mais de 60 varreduras completas** da tabela.
No Cenário B, com 5 GB de dados:

| | |
|---|---|
| Custo de uma varredura de 5 GB | ~625.000 RCUs ≈ US$ 0,156 |
| Varreduras por lançamento | > 60 |
| Custo por lançamento | ~US$ 9,38 |
| **Custo mensal com 5 milhões de lançamentos** | **~US$ 46.900.000** |

O número é absurdo justamente porque **o sistema nunca chegaria lá**: o Lambda
estoura o timeout de 60 s muito antes, as mensagens caem na DLQ e o serviço para.
Mas ele dimensiona a distância entre o desenho atual e o alvo — não é uma questão
de otimização, é de ordem de grandeza.

> **Na entrevista:** *"Traduzi o problema de escalabilidade para custo, porque é
> a linguagem que o patrocinador entende. A diferença entre o desenho atual e o
> alvo não é 20% de eficiência — são seis ordens de grandeza."*

---

## 7. Serverless × containers: onde está o ponto de virada

O [ADR-001](adr/0001-serverless-e-granularidade-dos-servicos.md) escolheu
serverless e prometeu revisitar a decisão em volume alto e constante. Aqui está o
número.

**Alternativa em Fargate**, com paridade de disponibilidade (2 tarefas por serviço,
em AZs distintas):

| Item | Custo/mês |
|---|---:|
| 4 tarefas × 0,5 vCPU × 730 h | US$ 59,10 |
| 4 tarefas × 1 GB × 730 h | US$ 12,98 |
| Application Load Balancer | US$ 22,00 |
| **Total (independente do volume)** | **US$ 94,08** |

**Comparação (compute + entrada, com HTTP API):**

| Volume/mês | Serverless | Fargate | Vencedor |
|---|---:|---:|---|
| 10 mil req | US$ 0,03 | US$ 94,08 | 🟢 Serverless (3.000×) |
| 1 milhão | US$ 2,00 | US$ 94,08 | 🟢 Serverless (47×) |
| 15 milhões | US$ 27,89 | US$ 94,08 | 🟢 Serverless (3,4×) |
| 50 milhões | US$ 92,96 | US$ 94,08 | 🟡 Equivalente |
| 100 milhões | US$ 185,00 | US$ 94,08 | 🔴 Fargate (2×) |

**Ponto de virada: ~50 milhões de requisições/mês**, ou cerca de 19 req/s
sustentadas 24×7.

O pico de 50 rps do enunciado ocorre em **dias de pico**, não continuamente.
Se fosse contínuo, seriam ~130 milhões de requisições/mês e a decisão deveria ser
revista. **A escolha por serverless está correta para o perfil descrito — e agora
existe o número que a sustenta**, em vez de uma preferência.

---

## 8. Licenças

| Item | Custo |
|---|---|
| Python, boto3, redis-py, aws-xray-sdk | US$ 0 — código aberto (PSF, Apache 2.0, MIT) |
| pytest, moto, freezegun | US$ 0 — código aberto |
| k6 | US$ 0 — AGPL 3.0, uso local |
| Serviços AWS gerenciados | Sem licença separada; incluído no preço de uso |
| **Total de licenças** | **US$ 0,00** |

**Ausência de licença é uma decisão arquitetural, não coincidência.** A pilha foi
escolhida sem custo de licença e sem dependência de fornecedor além da própria
AWS. Comparado a uma alternativa com Oracle, SQL Server Enterprise ou middleware
proprietário, a diferença é de dezenas de milhares de dólares por ano — e essa
comparação é justamente o argumento que sustenta o projeto de migração do
[legado](04-arquitetura-transicao.md), onde há licença de SQL Server e do ERP.

**Custo humano**, que costuma ser esquecido em comparação de infraestrutura: a
pilha gerenciada não exige DBA, administrador de sistema operacional nem
especialista em cluster. Para uma organização pequena, esse é frequentemente o
maior item de economia — e não aparece em nenhuma tabela de preço de nuvem.

---

## 9. Ressalvas

**Região.** Os valores são de `us-east-1`. **`sa-east-1` (São Paulo) é 30–50%
mais caro.** Para um comerciante brasileiro, latência e eventual exigência de
residência de dados (LGPD) podem obrigar a São Paulo — então some ~40% a todos os
totais. Esse é um trade-off de negócio, não técnico: latência e conformidade
contra custo.

**Nível gratuito.** Os cálculos **não** consideram o nível gratuito da AWS. Na
prática, o Cenário A ficaria quase inteiramente coberto nos primeiros 12 meses,
exceto pelos componentes fixos — que são justamente os que dominam a conta.

**Não incluído.** Transferência entre AZs, backups além do PITR, ambientes de
desenvolvimento e homologação (some ~40% para dev + homologação com
dimensionamento reduzido), e suporte AWS.

**Precisão.** Os itens de KMS e CloudFront são os mais sensíveis a premissa —
KMS depende do padrão de cache de chave de dados do DynamoDB, e CloudFront do
volume real de transferência. Ambos foram estimados de forma conservadora.
