# ADR-001 — Serverless e granularidade dos serviços

- **Status:** Aceito
- **Contexto de decisão:** definição do estilo arquitetural e do número de serviços

## Contexto

O sistema precisa registrar lançamentos financeiros e apurar o saldo diário.
O enunciado exige que o registro continue disponível se a apuração cair, e informa
um pico de 50 req/s na apuração.

Duas perguntas independentes, frequentemente confundidas:

1. **Quantos serviços?** (granularidade)
2. **Onde cada serviço roda?** (modelo de execução)

## Decisão

**Dois serviços**, correspondentes aos dois bounded contexts identificados em
[domínios e capacidades](../01-dominios-e-capacidades.md), **executados como
funções serverless** (AWS Lambda) atrás de API Gateway.

### Sobre a granularidade

Dois, não um; dois, não seis.

**Por que não um monólito:** o requisito de isolamento de falhas é explícito no
enunciado. Num processo único, um pico de consolidação consome a mesma memória,
o mesmo pool de conexões e o mesmo processo do registro. Não existe forma de
garantir o isolamento pedido dentro de um único deployable — seria preciso
reescrever o requisito, não a implementação.

**Por que não mais de dois:** a fronteira de um serviço deve seguir a fronteira
de um bounded context. Só C1 (registro) e C2 (apuração) são capacidades core com
requisitos de disponibilidade distintos. Fatiar "categorização" ou "relatórios"
em serviços próprios criaria fronteiras de rede sem fronteira de negócio
correspondente — custo de operação e latência sem benefício. **Microsserviço é
resposta a um problema organizacional e de acoplamento, não uma meta.**

### Sobre o modelo de execução

| Critério | Serverless (Lambda) | Containers (ECS/Fargate) |
|---|---|---|
| Perfil de carga com picos e vales | ✅ Escala ao evento, paga por uso | ❌ Capacidade ociosa entre picos |
| Custo em volume baixo | ✅ Tende a zero | ❌ Piso fixo por tarefa |
| Custo em volume alto e constante | ❌ Mais caro por requisição | ✅ Mais barato |
| Esforço de operação | ✅ Sem patch de SO nem escalonamento | ❌ Cluster, imagens, autoscaling |
| Latência de partida a frio | ❌ 200 ms–1 s | ✅ Processo sempre quente |
| Duração máxima | ❌ 15 min | ✅ Sem limite |
| Portabilidade | ❌ Acoplado ao provedor | ✅ Container roda em qualquer lugar |

O perfil descrito no enunciado — "dias de pico" com 50 rps e volume menor no
restante — é o caso em que serverless vence com folga: a capacidade acompanha a
demanda sem provisionamento antecipado, e não se paga pelo vale.

## Alternativas consideradas e descartadas

**Monólito modular em Fargate.** Descartado por não atender ao requisito de
isolamento. Seria a escolha certa se o requisito não existisse: menos partes
móveis, transação local em vez de consistência eventual, depuração mais simples.
Vale registrar que **o requisito de isolamento é o que custa a complexidade
distribuída** — se o negócio decidisse abrir mão dele, o monólito seria melhor.

**Kubernetes (EKS).** Descartado por desproporção. Dois serviços não justificam o
custo cognitivo e operacional de um cluster. Faria sentido com dezenas de
serviços, times múltiplos, ou exigência de portabilidade entre nuvens.

**Step Functions para orquestrar a consolidação.** Descartado por acoplamento: o
consolidado é uma reação a um evento, não um processo de várias etapas
coordenadas. Orquestração introduziria um controlador que precisa conhecer os dois
domínios — o oposto da coreografia por evento que o desenho busca.

## Consequências

**Positivas**
- Escala automática sem gestão de capacidade
- Custo proporcional ao uso, quase zero em repouso
- Isolamento de falhas viável (com os controles do [ADR-004](0004-agregacao-incremental-vs-recalculo.md))
- Observabilidade nativa (CloudWatch, X-Ray)

**Negativas e como são mitigadas**
- **Partida a frio.** Aceitável no registro (200–800 ms, dentro do SLO p95 de
  200 ms para invocações quentes). Se virar problema, `ProvisionedConcurrency`
  no horário comercial resolve, ao custo de capacidade reservada.
- **Acoplamento ao provedor.** Real, mas mitigado: a lógica de domínio (validação,
  cálculo de saldo) não tem dependência da AWS e é testada sem ela. O que está
  acoplado é a camada de adaptação.
- **Limite de 15 minutos.** Irrelevante para operações de milissegundos. Relevante
  para reprocessamento em massa, que deve ser feito em lote fragmentado.
- **Depuração distribuída.** Mitigada com X-Ray e correlação por `requestId`.

## Ponto de reavaliação

Esta decisão deve ser revisitada se o tráfego se tornar **constante e alto**.
Existe um ponto de inflexão a partir do qual Fargate fica mais barato que Lambda
para a mesma carga. O número está calculado em
[estimativa de custos §7](../05-estimativa-custos.md#7-serverless--containers-onde-está-o-ponto-de-virada):
**o ponto de virada é ~50 milhões de requisições/mês**, ou cerca de 19 req/s
sustentadas 24×7. O pico de 50 rps do enunciado ocorre em dias de pico, não
continuamente — então a decisão por serverless se sustenta para o perfil descrito.
