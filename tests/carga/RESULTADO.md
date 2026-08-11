# Resultado do teste de carga — NFR de 50 rps

> *"Em dias de picos, o serviço de consolidado diário recebe 50 requisições por
> segundo, com no máximo 5% de perda de requisições."* — enunciado do desafio

Executado em **11/08/2026** contra a stack implantada em `us-east-1`.

---

## Veredito: **ATENDE**

| Métrica | Medido | Meta | |
|---|---:|---:|:---:|
| Taxa sustentada | 49,77 req/s | 50 req/s | ✅ |
| Perda de requisições | **0,00%** | ≤ 5% | ✅ |
| Latência p95 | 177 ms | < 1000 ms | ✅ |
| Requisições totais | 8.966 | — | |

Distribuição completa da latência:

| | ms |
|---|---:|
| mínima | 150 |
| mediana | 164 |
| p90 | 173 |
| p95 | 177 |
| **máxima** | **3.801** |

---

## Como foi montado

| | |
|---|---|
| Ferramenta | k6, executor `constant-arrival-rate` |
| Duração | 3 minutos |
| Endpoint | `GET /consolidado?data=<aleatória>` |
| Autenticação | JWT com escopo `consolidado:read` |
| **Volume na base** | **20.000 lançamentos** distribuídos por 28 dias |

O executor `constant-arrival-rate` mantém a taxa de chegada fixa mesmo quando o
serviço fica lento. Um cenário baseado em usuários virtuais reduziria a carga
sozinho ao encontrar latência alta, escondendo justamente a degradação que se
quer medir — o efeito conhecido como *coordinated omission*.

A base foi populada de propósito. Contra uma tabela vazia o teste passaria
trivialmente, e seria uma medição verdadeira de um cenário irrelevante.

---

## O que este número NÃO prova

Ler o veredito isolado leva a uma conclusão errada. Duas ressalvas importam mais
que o "ATENDE".

### 1. O teste aquece

O cenário sorteia datas dentro de **28 dias distintos**. A primeira consulta a
uma data ainda não consolidada dispara o caminho caro — `calculate_saldo_diario`
varre a tabela inteira — mas **grava o resultado**. Da segunda em diante, aquela
data responde por `GetItem`, que é O(1).

Com 8.966 requisições sobre 28 datas, cerca de **28 requisições pagaram o scan e
8.938 não pagaram**. Ou seja: 99,7% da medição reflete o caminho barato.

**A latência máxima de 3.801 ms é a assinatura desses primeiros scans** — 25
vezes a mediana. É o custo real da varredura sobre 20 mil itens aparecendo nas
poucas requisições que o pagaram.

### 2. O gargalo do ADR-004 está em outro eixo

O [ADR-004](../../docs/adr/0004-agregacao-incremental-vs-recalculo.md) descreve
amplificação de trabalho no **caminho de escrita**: cada lançamento novo dispara,
no consumidor SQS, um recálculo que percorre até 30 dias com duas varreduras por
dia.

Esse custo é proporcional ao **volume de lançamentos**, não à taxa de leitura.
Este teste mede leitura. **Os dois não se contradizem — medem coisas diferentes.**

Um teste que atacasse o ADR-004 precisaria sustentar alta taxa de *escrita* e
observar a profundidade da fila e a idade da mensagem mais antiga, não a latência
do `GET`.

---

## Conclusão honesta

**O requisito do enunciado, como está escrito, é atendido.** O serviço de
consolidado sustenta 50 req/s de leitura sem perda e com p95 de 177 ms, sobre uma
base de 20 mil lançamentos.

**Isso não invalida o ADR-004.** A preocupação daquele documento continua de pé, e
a máxima de 3,8 s é evidência a favor dela: quando o caminho de recálculo é
exercitado, ele custa 25 vezes mais que o caminho normal. Numa base de produção,
com milhões de lançamentos e datas sendo consultadas pela primeira vez, esse
número cresce proporcionalmente ao tamanho da tabela.

O que o teste demonstra é que **a materialização funciona**: o consolidado é
calculado uma vez e servido barato depois. O risco está em quantas vezes esse
"uma vez" acontece — que é exatamente o que a agregação incremental elimina.

---

## Limitações desta medição

| Limitação | Efeito |
|---|---|
| Apenas 28 datas distintas | Taxa de acerto irreal; produção tem dispersão maior |
| Cache Redis ativo | Parte dos ganhos vem do cache, não do banco |
| 20 mil lançamentos | Duas a três ordens de grandeza abaixo de produção |
| Origem única | Não exercita o rate limiting por identidade do WAF |
| Caminho de escrita não medido | O gargalo do ADR-004 permanece não testado |

## Como reproduzir

```bash
python scripts/popular_base_de_carga.py --tabela <tabela-lancamentos> --quantidade 20000
```

```bash
k6 run -e API_URL=<url-da-api> -e TOKEN=<token> tests/carga/consolidado-50rps.js
```

Dados brutos em [`resultado-50rps.json`](resultado-50rps.json).
