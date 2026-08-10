# Estratégia de testes

## Como rodar

```bash
python tarefas.py instalar    # cria o venv e instala as dependências
python tarefas.py testar      # roda tudo
python tarefas.py cobertura   # roda com relatório de cobertura
python tarefas.py lacunas     # mostra só as lacunas conhecidas do MVP
```

> Em Linux e macOS, o `Makefile` oferece os mesmos comandos (`make test`, `make cov`...).

Nenhum teste precisa de conta AWS, credencial ou Docker. Os serviços da AWS são
simulados em memória pelo [moto](https://github.com/getmoto/moto).

## Resultado atual

| Suíte | Testes | Cobertura |
|---|---|---|
| Lançamentos | 50 passando + 3 lacunas | 83% |
| Consolidado | 44 passando + 3 lacunas | 74% |
| **Total** | **94 passando + 6 lacunas** | — |

## Por que dois processos separados

`tarefas.py testar` invoca o pytest **duas vezes**, uma por serviço. Isso é deliberado.

Lançamentos e consolidado são unidades de deploy independentes: cada uma é
empacotada com seus módulos na raiz do `.zip` do Lambda, e as duas têm arquivos de
mesmo nome (`main.py`, `utils.py`, `operacoes.py`, `sqs.py`, `configuration.py`).
Carregar as duas no mesmo processo Python faria uma sobrescrever a outra em
`sys.modules` — uma colisão que **não existe em produção**, onde cada função roda
isolada. Processos separados reproduzem o isolamento real.

`tests/conftest.py` detecta a tentativa de rodar as duas juntas e falha com essa
explicação, em vez de deixar quebrar com um erro obscuro.

## Camadas

**Unitários de domínio** — regras de negócio puras, sem infraestrutura.
`test_validacao.py`, `test_calculo_saldo.py`. São os testes mais valiosos: rápidos,
estáveis e cobrindo o que o negócio realmente pediu.

**Integração** — o `lambda_handler` completo contra DynamoDB, SQS, S3 e
EventBridge simulados. `test_api_lancamentos.py`, `test_consumo_sqs.py`,
`test_api_consolidado.py`. Verificam contrato HTTP, persistência e publicação de
eventos.

**Carga** — `carga/consolidado-50rps.js` (k6), que verifica o requisito não-funcional
numérico do enunciado. Ver §Teste de carga.

## Lacunas conhecidas (`xfail(strict=True)`)

Seis testes descrevem comportamento que a arquitetura alvo exige e o MVP ainda não
entrega. Eles usam `xfail(strict=True)`:

- enquanto o defeito existe, o teste é reportado como **XFAIL** (esperado, não quebra o build);
- quando a correção entrar, ele passa e o pytest acusa **XPASS como FALHA**,
  obrigando a remover o marcador.

Isso impede que uma lacuna seja silenciosamente esquecida como resolvida — e
documenta o roadmap dentro do código, não num quadro à parte que envelhece.

| Lacuna | Teste |
|---|---|
| Sem idempotência no registro | `lancamentos/test_lacunas_conhecidas.py` |
| Escrita dupla sem transação (evento pode se perder) | `lancamentos/test_lacunas_conhecidas.py` |
| Dinheiro serializado como float | `lancamentos/test_lacunas_conhecidas.py` |
| Dia de negócio apurado em UTC | `consolidado/test_lacunas_conhecidas.py` |
| Recálculo por varredura não escala a 50 rps | `consolidado/test_lacunas_conhecidas.py` |
| Consolidado lê a base de Lançamentos | `consolidado/test_lacunas_conhecidas.py` |

Contexto de cada uma em [docs/02-requisitos-e-nfrs.md](../docs/02-requisitos-e-nfrs.md#3-rastreabilidade-das-lacunas).

## Defeitos que os testes encontraram

Escrever a suíte revelou quatro defeitos reais no código original — todos
corrigidos, todos com teste de regressão:

1. **`ValidationError` nunca foi definida.** Usada em 6 pontos de
   `apps/lancamentos/utils.py`, resultava em `NameError`. Toda validação
   respondia ao cliente `"name 'ValidationError' is not defined"` em vez da
   mensagem de negócio.
2. **`raise "string"` em 8 lugares.** Python exige uma instância de exceção;
   isso levanta `TypeError` e **descarta a causa raiz** justamente no momento do
   diagnóstico.
3. **`Decimal("abc")` escapava da validação.** `InvalidOperation` herda de
   `ArithmeticError`, não de `ValueError`, então não era capturada — e
   `{"valor": "abc"}` virava HTTP 500 em vez de 400, disparando alarme de erro
   indevidamente.
4. **`valor: 0` devolvia a mensagem errada.** A checagem por veracidade
   (`not data[field]`) tratava zero como campo ausente, respondendo "campo
   obrigatório" quando o correto é "valor deve ser maior que zero".

## Teste de carga

Verifica o único requisito do enunciado com meta numérica:

> *"Em dias de picos, o serviço de consolidado diário recebe 50 requisições por
> segundo, com no máximo 5% de perda."*

```bash
API_URL=https://<id>.execute-api.<regiao>.amazonaws.com/dev python tarefas.py carga
```

O cenário usa `constant-arrival-rate`, que mantém a taxa de chegada fixa mesmo
quando o serviço fica lento. Um cenário baseado em usuários virtuais reduziria a
carga sozinho ao encontrar latência alta, escondendo exatamente a degradação que
se quer medir — o efeito conhecido como *coordinated omission*.

Os limiares são porta de qualidade: se estourarem, o k6 sai com código 1 e o
pipeline falha. O requisito não-funcional vira verificação executável, não
parágrafo de documento.

> ⚠️ **Este teste ainda não foi executado contra um ambiente implantado.** Pela
> análise de custo por evento documentada em
> [ADR-004](../docs/adr/0004-agregacao-incremental-vs-recalculo.md), a expectativa
> é que o desenho atual **não** atinja a meta — cada mensagem dispara dezenas de
> varreduras completas de tabela. O teste existe para tornar essa hipótese
> verificável e para validar a correção quando ela entrar.

## O que ainda falta

- Testes de contrato contra `openapi_spec.txt`, garantindo que a implementação
  não divirja da especificação publicada
- Testes de resiliência (injeção de falha: fila indisponível, banco lento)
- Execução automatizada em pipeline de CI
