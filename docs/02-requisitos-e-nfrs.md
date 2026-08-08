# Requisitos funcionais e não-funcionais

> Requisito obrigatório do desafio: *"Refinamento do levantamento de requisitos
> funcionais e não funcionais"*.

O enunciado entrega duas linhas de requisito de negócio e um parágrafo de
requisito não-funcional. Este documento é o **refinamento** disso: transforma
enunciado em critério verificável.

A régua adotada: **um requisito não-funcional sem número não é requisito, é
intenção.** Toda meta aqui tem valor, forma de medir e consequência quando é
violada.

---

## 1. Requisitos funcionais

### 1.1 Domínio de Lançamentos

| ID | Requisito | Critério de aceite | Verificação |
|---|---|---|---|
| RF-01 | Registrar lançamento de crédito ou débito | Retorna 201 com identificador único; item persistido | `test_lancamento_valido_retorna_201`, `test_lancamento_e_persistido_no_dynamodb` |
| RF-02 | Rejeitar lançamento inválido | Retorna 400 com mensagem de negócio legível; nada é gravado | `TestErrosDeEntrada` |
| RF-03 | Valor deve ser positivo | Zero e negativo rejeitados; o sinal vem do `tipo` | `test_valor_negativo_e_rejeitado`, `test_valor_zero_e_rejeitado` |
| RF-04 | Preservar precisão monetária | Valor gravado como `Decimal`, sem perda de centavos | `test_valor_e_gravado_como_decimal_no_banco` |
| RF-05 | Consultar lançamento por identificador | 200 com o lançamento; 404 se inexistente | `TestConsultas` |
| RF-06 | Listar com filtro por período, tipo e categoria | Resultado filtrado, paginado e com resumo financeiro | `test_listagem_traz_resumo_financeiro`, `test_filtro_por_tipo` |
| RF-07 | Publicar evento de lançamento criado | Mensagem na fila de consolidação | `test_evento_e_publicado_na_fila_de_consolidacao` |
| RF-08 | Cancelamento por status, nunca por exclusão física | Item permanece com `status` alterado | Modelagem (`status: ATIVO`) |
| RF-09 | Não duplicar lançamento em retentativa do cliente | Mesma `Idempotency-Key` ⇒ um único lançamento | ⚠️ `test_repeticao_com_mesma_chave_de_idempotencia_nao_duplica` (lacuna P1) |

### 1.2 Domínio de Consolidação

| ID | Requisito | Critério de aceite | Verificação |
|---|---|---|---|
| RF-10 | Apurar saldo diário | `saldo_final = saldo_inicial + créditos − débitos` | `TestCalculoDoSaldoDiario` |
| RF-11 | Transportar saldo entre dias | `saldo_inicial(D) = saldo_final(D−1)` | `test_saldo_anterior_vem_do_fechamento_do_dia_anterior` |
| RF-12 | Dia sem movimento preserva o saldo | Saldo anterior mantido, sem zerar | `test_dia_sem_movimento_preserva_o_saldo_anterior` |
| RF-13 | Ignorar lançamentos não ativos | Cancelados fora do saldo | `test_lancamento_nao_ativo_e_ignorado` |
| RF-14 | Consultar saldo de uma data | 200 com a posição; zeros se não houve movimento | `TestSaldoDiario` |
| RF-15 | Relatório consolidado por período | Agregado do período, com detalhamento opcional | `TestRelatorioPorPeriodo` |
| RF-16 | Arquivar relatório para histórico | Objeto gravado no S3, cifrado com KMS | `test_relatorio_pode_ser_arquivado_no_s3` |
| RF-17 | Reprocessamento não altera o resultado | Mesma mensagem processada 2× ⇒ mesmo saldo | `test_reprocessar_a_mesma_mensagem_nao_altera_o_saldo` |
| RF-18 | Saldo apurado no dia de negócio do comerciante | Venda às 22h BRT pertence ao dia local | ⚠️ `test_venda_da_noite_pertence_ao_dia_local_do_comerciante` (lacuna P1) |

---

## 2. Requisitos não-funcionais

### 2.1 Os dois requisitos explícitos do enunciado

> *"O serviço de controle de lançamento não deve ficar indisponível se o sistema
> de consolidado diário cair."*

**RNF-01 — Isolamento de falhas entre os domínios.**

Refinado em cinco condições verificáveis. É insuficiente dizer "usei fila": a
fila resolve o acoplamento *temporal*, mas sobram quatro recursos compartilhados
que reintroduzem o acoplamento por outro caminho.

| | Condição | Estado | Como garantir |
|---|---|---|---|
| a | Lançamentos não faz chamada síncrona a Consolidação | ✅ Atendido | Comunicação só por evento |
| b | Falha do consumidor não perde evento | ✅ Atendido | Fila com retentativa + DLQ (14 dias) |
| c | Consolidação não drena a concorrência de Lançamentos | ✅ Atendido | `ReservedConcurrentExecutions` no produtor + `MaximumConcurrency` no consumidor |
| d | Consolidação não consome a cota de requisições de Lançamentos | ✅ Atendido | `MethodSettings` por recurso: 200 rps em lançamentos, 80 rps no consolidado |
| e | Consolidação não compete por capacidade na base de Lançamentos | ❌ **Lacuna** | Store próprio; hoje o consolidado varre a tabela de lançamentos |

> As condições (c), (d) e (e) são o modo de falha mais provável deste requisito e
> estão detalhadas em [ADR-004](adr/0004-agregacao-incremental-vs-recalculo.md).

> *"Em dias de picos, o serviço de consolidado diário recebe 50 requisições por
> segundo, com no máximo 5% de perda de requisições."*

**RNF-02 — Capacidade do serviço de consolidado.**

| Atributo | Meta |
|---|---|
| Taxa sustentada | 50 req/s |
| Perda máxima aceita | 5% |
| Janela do pico | 3 minutos sustentados (premissa: ver §4) |
| Verificação | `tests/carga/consolidado-50rps.js` (k6) |

Este é o **único requisito do enunciado com meta numérica**, então é verificado
com número. O cenário k6 usa `constant-arrival-rate`, que mantém a taxa de
chegada fixa mesmo quando o serviço fica lento — um cenário baseado em usuários
virtuais reduziria a carga sozinho e esconderia justamente a degradação que se
quer medir (*coordinated omission*).

Os limiares do k6 são porta de qualidade: se estourarem, o processo sai com
código 1 e o pipeline falha.

> ⚠️ **O desenho atual não atende RNF-02.** Cada mensagem dispara `scan` da
> tabela de lançamentos e um recálculo em cascata de até 30 dias com 2 scans por
> dia — mais de 60 varreduras por evento. A 50 rps o Lambda estoura o timeout
> antes de qualquer coisa. Correção em [ADR-004](adr/0004-agregacao-incremental-vs-recalculo.md),
> coberta por `test_um_evento_nao_pode_disparar_varredura_da_tabela`.

### 2.2 SLIs e SLOs

Indicadores derivados da criticidade de cada domínio (ver
[domínios e capacidades](01-dominios-e-capacidades.md)). São eles que dão base
aos alarmes — sem SLO, todo limiar de alarme é arbitrário.

| Domínio | SLI | SLO | Justificativa de negócio |
|---|---|---|---|
| Lançamentos | Disponibilidade (respostas não-5xx) | **99,9%** / mês (≈43 min) | Caminho crítico de receita: indisponível = venda não registrada, irrecuperável |
| Lançamentos | Latência p95 | **< 200 ms** | Operação no PDV; acima disso o operador percebe travamento |
| Lançamentos | Latência p99 | **< 500 ms** | Limita a cauda |
| Lançamentos | Durabilidade do evento | **100%** | Todo lançamento gravado gera evento — sem exceção |
| Consolidação | Disponibilidade | **99,5%** / mês (≈3,6 h) | Relatório tolera degradação; é o que o RNF-01 autoriza |
| Consolidação | Latência p95 (leitura) | **< 1 s** | Consulta de relatório, não operação de balcão |
| Consolidação | Taxa de sucesso em pico | **≥ 95%** a 50 rps | RNF-02, literal do enunciado |
| Consolidação | **Defasagem do saldo** p95 | **< 30 s** | Janela de consistência eventual declarada ao cliente |
| Consolidação | Mensagens em DLQ | **0** | Qualquer mensagem em DLQ = saldo errado; exige intervenção |

**A defasagem do saldo é o SLI mais importante do sistema** e o menos óbvio.
Ele mede o intervalo entre "o lançamento foi aceito" e "ele aparece no saldo" —
que é exatamente o preço pago pela escolha de consistência eventual
([ADR-005](adr/0005-consistencia-eventual.md)). Sem medi-lo, a escolha vira
promessa não verificada.

**Orçamento de erro:** o SLO de 99,9% concede 43 minutos de indisponibilidade por
mês. Enquanto houver orçamento, novas funcionalidades entram; quando o orçamento
se esgotar, o time congela features e trabalha confiabilidade. Isso torna a
decisão objetiva, em vez de discussão de opinião.

### 2.3 Demais atributos de qualidade

| Categoria | Requisito | Meta | Estado |
|---|---|---|---|
| **Segurança** | Autenticação em todos os endpoints | Nenhum endpoint anônimo | ✅ Lambda Authorizer JWT nos 6 endpoints de negócio |
| | Autorização por escopo | `lancamentos:write`, `consolidado:read` | ✅ Escopo exigido por recurso, negação por padrão |
| | Criptografia em repouso | KMS em DynamoDB, S3, SQS, Redis | ✅ Atendido |
| | Criptografia em trânsito | TLS 1.2+ | ✅ Atendido |
| | Proteção de borda | WAF com rate limiting | ✅ Atendido |
| | Menor privilégio | Uma role por função | ✅ Três roles distintas; permissões de EC2 perigosas removidas |
| | Isolamento multi-tenant | Dados segregados por comerciante | ❌ Não existe `comerciante_id` no modelo |
| **Escalabilidade** | Escala horizontal automática | Sem intervenção manual | ✅ Serverless |
| | Capacidade de escrita | 200 lançamentos/s | ✅ DynamoDB on-demand |
| **Resiliência** | Tolerância a falha de zona | Multi-AZ | ⚠️ NAT Gateway em AZ única (SPOF) |
| | Recuperação de mensagem | DLQ com 14 dias | ✅ Atendido |
| | Degradação graciosa do cache | Cache fora ⇒ segue para o banco | ✅ Atendido |
| | RPO | ≤ 5 min | ✅ PITR habilitado |
| | RTO | ≤ 1 h | ⚠️ Procedimento não documentado |
| **Observabilidade** | Rastreamento distribuído | Ponta a ponta | ✅ X-Ray ativo |
| | Alarme de DLQ | Notificação imediata | ✅ Limiar zero, severidade 1 |
| | Alarme de defasagem | `ApproximateAgeOfOldestMessage` | ✅ Limiar 30s, alinhado ao SLO |
| | Log de acesso da API | Entregue ao CloudWatch | ✅ Log group dedicado com retenção |
| | Retenção de log | 30 dias | ✅ Parametrizado em todos os log groups |
| **Manutenibilidade** | Cobertura de testes | > 70% | ✅ 83% (lançamentos), 74% (consolidado) |
| | Infra como código | 100% | ✅ CloudFormation |
| | Execução local | Um comando, sem conta AWS | ✅ `python tarefas.py subir && python tarefas.py rodar` |
| | Pipeline automatizado | Lint, teste, análise, deploy | ❌ Ausente |

---

## 3. Rastreabilidade das lacunas

Toda lacuna acima tem teste escrito que falha de propósito (`xfail(strict=True)`).
Quando a correção entrar, o teste passa e o pytest acusa **XPASS como falha**,
obrigando a atualizar o marcador. É o mecanismo que impede uma lacuna de ser
esquecida como se estivesse resolvida.

```bash
python tarefas.py lacunas
```

| Lacuna | Prioridade | Teste | ADR |
|---|---|---|---|
| Sem idempotência no registro | P1 | `test_repeticao_com_mesma_chave_de_idempotencia_nao_duplica` | — |
| Escrita dupla (banco + fila) sem transação | P1 | `test_falha_ao_publicar_evento_nao_pode_responder_sucesso` | [ADR-003](adr/0003-comunicacao-assincrona.md) |
| Dinheiro serializado como float | P1 | `test_valor_na_resposta_preserva_precisao_decimal` | — |
| Dia de negócio em UTC | P1 | `test_venda_da_noite_pertence_ao_dia_local_do_comerciante` | — |
| Recálculo por varredura não escala | P1 | `test_um_evento_nao_pode_disparar_varredura_da_tabela` | [ADR-004](adr/0004-agregacao-incremental-vs-recalculo.md) |
| Base compartilhada entre contextos | P1 | `test_consolidado_nao_deve_acessar_a_tabela_de_lancamentos` | [ADR-004](adr/0004-agregacao-incremental-vs-recalculo.md) |

---

## 4. Premissas assumidas

O enunciado é deliberadamente enxuto. Onde faltou informação, assumi o seguinte —
e registro para que o avaliador julgue o **raciocínio**, não adivinhe a intenção:

| # | Premissa | Fundamento | Impacto se estiver errada |
|---|---|---|---|
| P1 | Volume de ~200 lançamentos/s no pico | Simetria com os 50 rps de leitura informados | Muda o dimensionamento, não a arquitetura |
| P2 | Pico dura minutos, não horas | "Dias de pico" no varejo = horário de movimento | Pico longo favoreceria capacidade provisionada |
| P3 | Fuso de referência: America/Sao_Paulo | Comerciante brasileiro | Se for multi-região, o fuso vira atributo do comerciante |
| P4 | Retenção de lançamentos: 5 anos | Prazo fiscal brasileiro | Muda custo de armazenamento e estratégia de arquivamento |
| P5 | Sistema evoluirá para multi-comerciante | "Um comerciante" é o caso de uso, não o limite do produto | Se for mesmo mono-tenant, a chave composta é custo desnecessário |
| P6 | Consistência eventual é aceitável no saldo | Decorre do RNF-01: exigir saldo síncrono contradiz o isolamento pedido | Se o negócio exigir saldo imediato, o RNF-01 precisa ser renegociado |

**P6 merece destaque na conversa com o negócio.** O enunciado pede, ao mesmo
tempo, que os serviços sejam independentes e que exista um saldo consolidado.
Essas duas coisas são conciliáveis apenas com consistência eventual — não há
desenho que entregue isolamento total *e* saldo síncrono. Explicitar esse
trade-off é responsabilidade do arquiteto, não do time de desenvolvimento.
