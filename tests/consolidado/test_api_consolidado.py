"""
API de consulta do consolidado: saldo diário, relatório por período e métricas.
"""
import json
from decimal import Decimal

import pytest
from main import lambda_handler


def corpo(resposta):
    return json.loads(resposta["body"])


class TestSaldoDiario:
    def test_consulta_por_data_retorna_o_saldo(self, evento_http, gravar_lancamento):
        gravar_lancamento("CREDITO", "500.00", data="2025-07-30T10:00:00")
        gravar_lancamento("DEBITO", "200.00", data="2025-07-30T11:00:00")

        resposta = lambda_handler(evento_http(query={"data": "2025-07-30"}), None)

        assert resposta["statusCode"] == 200
        dados = corpo(resposta)["data"]
        assert dados["total_creditos"] == 500.0
        assert dados["total_debitos"] == 200.0
        assert dados["saldo_final"] == 300.0

    def test_data_sem_movimento_retorna_saldo_zerado(self, evento_http):
        """Não é 404: "não houve movimento" é uma resposta de negócio válida e
        diferente de "a data não existe"."""
        resposta = lambda_handler(evento_http(query={"data": "2025-01-15"}), None)

        assert resposta["statusCode"] == 200
        assert corpo(resposta)["data"]["saldo_final"] == 0

    def test_data_em_formato_invalido_retorna_400(self, evento_http):
        resposta = lambda_handler(evento_http(query={"data": "30-07-2025"}), None)

        assert resposta["statusCode"] == 400

    def test_sem_parametro_consulta_o_dia_corrente(self, evento_http):
        resposta = lambda_handler(evento_http(query=None), None)

        assert resposta["statusCode"] == 200
        assert corpo(resposta)["tipo"] == "saldo_atual"

    def test_resposta_informa_quando_o_dado_foi_atualizado(self, evento_http, gravar_lancamento):
        """Em arquitetura de consistência eventual, o cliente precisa saber a
        idade do dado que recebeu para decidir se confia nele."""
        gravar_lancamento("CREDITO", "10.00", data="2025-07-30T10:00:00")

        resposta = lambda_handler(evento_http(query={"data": "2025-07-30"}), None)

        assert corpo(resposta)["data"]["ultima_atualizacao"]


class TestRelatorioPorPeriodo:
    def test_relatorio_agrega_o_periodo(self, evento_http, gravar_lancamento):
        gravar_lancamento("CREDITO", "100.00", data="2025-07-29T10:00:00")
        gravar_lancamento("CREDITO", "200.00", data="2025-07-30T10:00:00")

        resposta = lambda_handler(
            evento_http("/consolidado/relatorio",
                        query={"data_inicio": "2025-07-29", "data_fim": "2025-07-30"}),
            None,
        )

        assert resposta["statusCode"] == 200
        resumo = corpo(resposta)["data"]["resumo_financeiro"]
        assert resumo["total_creditos_periodo"] == 300.0
        assert corpo(resposta)["data"]["periodo"]["quantidade_dias"] == 2

    def test_parametros_obrigatorios_ausentes_retornam_400(self, evento_http):
        resposta = lambda_handler(evento_http("/consolidado/relatorio", query={}), None)

        assert resposta["statusCode"] == 400
        assert "obrigat" in corpo(resposta)["message"].lower()

    def test_periodo_invertido_retorna_400(self, evento_http):
        resposta = lambda_handler(
            evento_http("/consolidado/relatorio",
                        query={"data_inicio": "2025-07-30", "data_fim": "2025-07-01"}),
            None,
        )

        assert resposta["statusCode"] == 400

    def test_detalhamento_diario_sob_demanda(self, evento_http, gravar_lancamento):
        """O detalhe é opcional para não inflar a resposta do caso comum."""
        gravar_lancamento("CREDITO", "100.00", data="2025-07-29T10:00:00")

        resposta = lambda_handler(
            evento_http("/consolidado/relatorio",
                        query={"data_inicio": "2025-07-29", "data_fim": "2025-07-30",
                               "incluir_detalhes": "true"}),
            None,
        )

        assert len(corpo(resposta)["data"]["saldos_diarios"]) == 2

    def test_relatorio_pode_ser_arquivado_no_s3(self, evento_http, gravar_lancamento):
        gravar_lancamento("CREDITO", "100.00", data="2025-07-29T10:00:00")

        resposta = lambda_handler(
            evento_http("/consolidado/relatorio",
                        query={"data_inicio": "2025-07-29", "data_fim": "2025-07-29",
                               "salvar_s3": "true"}),
            None,
        )

        assert "arquivos" in corpo(resposta)
        assert corpo(resposta)["arquivos"]["s3_backup"]["key"].endswith(".json")


class TestMetricas:
    def test_metricas_do_periodo_padrao(self, evento_http, gravar_lancamento):
        from datetime import datetime, timezone

        hoje = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        gravar_lancamento("CREDITO", "100.00", data=f"{hoje}T10:00:00")

        resposta = lambda_handler(evento_http("/metricas"), None)

        assert resposta["statusCode"] == 200
        assert corpo(resposta)["data"]["resumo"]["total_lancamentos"] == 1

    @pytest.mark.parametrize("periodo", ["7d", "30d", "90d", "1y"])
    def test_periodos_suportados(self, evento_http, periodo):
        resposta = lambda_handler(evento_http("/metricas", query={"periodo": periodo}), None)

        assert resposta["statusCode"] == 200

    def test_periodo_nao_suportado_retorna_400(self, evento_http):
        resposta = lambda_handler(evento_http("/metricas", query={"periodo": "5min"}), None)

        assert resposta["statusCode"] == 400


class TestProtocoloHttp:
    def test_recurso_desconhecido_retorna_404(self, evento_http):
        resposta = lambda_handler(evento_http("/inexistente"), None)

        assert resposta["statusCode"] == 404

    def test_metodo_de_escrita_e_rejeitado(self, evento_http):
        """Este serviço é somente-leitura por desenho: a escrita pertence ao
        domínio de Lançamentos. Segregação de responsabilidade no nível da API."""
        resposta = lambda_handler(evento_http("/consolidado", metodo="POST"), None)

        assert resposta["statusCode"] == 405

    def test_preflight_cors_retorna_200(self, evento_http):
        resposta = lambda_handler(evento_http("/consolidado", metodo="OPTIONS"), None)

        assert resposta["statusCode"] == 200
