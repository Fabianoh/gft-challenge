/*
 * Teste de carga do requisito não-funcional explícito do desafio:
 *
 *   "Em dias de picos, o serviço de consolidado diário recebe 50 requisições
 *    por segundo, com no máximo 5% de perda de requisições."
 *
 * Este é o único requisito do enunciado com meta numérica, então ele é
 * verificado com número — não com afirmação em documento.
 *
 * Execução:
 *   k6 run -e API_URL=https://<id>.execute-api.<regiao>.amazonaws.com/dev \
 *          -e TOKEN=$(python scripts/gerar_token.py | grep ^eyJ) \
 *          tests/carga/consolidado-50rps.js
 *
 * O token é obrigatório: a API exige autenticação e sem ele o teste mede apenas
 * a velocidade com que o gateway devolve 401. O token vale uma hora — mais que
 * suficiente para os 3 minutos do cenário.
 *
 * O cenário usa `constant-arrival-rate`: o k6 mantém a taxa de chegada fixa em
 * 50 rps independentemente do tempo de resposta. Isso é essencial — um cenário
 * baseado em número de usuários virtuais reduz a carga sozinho quando o serviço
 * fica lento, escondendo exatamente a degradação que queremos medir
 * (o efeito conhecido como "coordinated omission").
 */
import http from 'k6/http';
import { check } from 'k6';
import { Rate, Trend } from 'k6/metrics';

const API_URL = __ENV.API_URL;
const TOKEN = __ENV.TOKEN;

if (!API_URL) {
  throw new Error('Defina API_URL: -e API_URL=https://<id>.execute-api.<regiao>.amazonaws.com/dev');
}
if (!TOKEN) {
  throw new Error(
    'Defina TOKEN. Sem token a API responde 401 e o teste mede a rejeicao, ' +
    'nao o servico. Gere um com: python scripts/gerar_token.py'
  );
}

const perdaDeRequisicoes = new Rate('perda_de_requisicoes');
const latenciaSaldoDiario = new Trend('latencia_saldo_diario', true);

export const options = {
  // p99 não é coletado por padrão; sem declarar aqui, o resumo imprime 0 ms.
  summaryTrendStats: ['min', 'avg', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],

  scenarios: {
    pico_consolidado: {
      executor: 'constant-arrival-rate',
      rate: 50,
      timeUnit: '1s',
      duration: '3m',
      // Pré-aloca VUs suficientes para sustentar a taxa mesmo com latência alta.
      preAllocatedVUs: 100,
      maxVUs: 400,
    },
  },

  // Estes limiares são o critério de aprovação. Se algum estourar, o k6 sai
  // com código 1 e o pipeline falha — o NFR vira porta de qualidade, não
  // parágrafo de documentação.
  thresholds: {
    // O requisito: no máximo 5% de perda.
    perda_de_requisicoes: ['rate<0.05'],
    // Margem de segurança sobre o requisito, para detectar degradação antes
    // de chegar ao limite contratado.
    http_req_failed: ['rate<0.05'],
    // SLO de latência declarado em docs/02-requisitos-e-nfrs.md.
    http_req_duration: ['p(95)<1000', 'p(99)<2000'],
  },
};

// Distribui as consultas ao longo de um mês para não bater sempre na mesma
// partição do DynamoDB nem sempre na mesma chave de cache — uma carga que
// consulta só uma data mede o cache, não o sistema.
function dataAleatoria() {
  const dia = 1 + Math.floor(Math.random() * 28);
  return `2025-07-${String(dia).padStart(2, '0')}`;
}

export default function () {
  const resposta = http.get(`${API_URL}/consolidado?data=${dataAleatoria()}`, {
    headers: {
      Accept: 'application/json',
      Authorization: `Bearer ${TOKEN}`,
    },
    tags: { endpoint: 'consolidado_saldo_diario' },
  });

  const ok = check(resposta, {
    'status 200': (r) => r.status === 200,
    // 401 e 403 em massa indicam token invalido ou expirado, nao degradacao do
    // servico. Sem esta distincao, um token errado se disfarcaria de falha de
    // capacidade e levaria a conclusao oposta sobre o requisito.
    'nao e falha de autenticacao': (r) => r.status !== 401 && r.status !== 403,
    'corpo tem saldo_final': (r) => {
      try {
        return r.json('data.saldo_final') !== undefined;
      } catch {
        return false;
      }
    },
  });

  perdaDeRequisicoes.add(!ok);
  latenciaSaldoDiario.add(resposta.timings.duration);
}

export function handleSummary(data) {
  const perda = data.metrics.perda_de_requisicoes?.values?.rate ?? 0;
  const p95 = data.metrics.http_req_duration?.values['p(95)'] ?? 0;
  const p99 = data.metrics.http_req_duration?.values['p(99)'] ?? 0;
  const total = data.metrics.http_reqs?.values?.count ?? 0;
  const throttled = data.metrics['http_req_failed']?.values?.passes ?? 0;

  const relatorio = `
RESULTADO — NFR "50 rps com no máximo 5% de perda"
==================================================
Requisições totais .... ${total}
Taxa de perda ......... ${(perda * 100).toFixed(2)}%   (limite: 5,00%)
Latência p95 .......... ${p95.toFixed(0)} ms          (SLO: 1000 ms)
Latência p99 .......... ${p99.toFixed(0)} ms          (SLO: 2000 ms)
Veredito .............. ${perda < 0.05 ? 'ATENDE' : 'NÃO ATENDE'}
`;

  return {
    stdout: relatorio,
    'tests/carga/resultado-50rps.json': JSON.stringify(data, null, 2),
  };
}
