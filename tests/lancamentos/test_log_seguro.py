"""
Garante que credenciais não chegam ao log.

Regressão da ameaça T4 (docs/07-seguranca.md): o handler registrava o evento
inteiro do API Gateway, cabeçalhos incluídos. Com autenticação ativa isso grava
o token `Bearer` em texto claro no CloudWatch, retido por 30 dias.
"""
import json

import pytest
from log_seguro import resumir_evento, resumir_evento_sqs

TOKEN = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJwZHYtMDAxIn0.assinatura-secreta"


@pytest.fixture
def evento_autenticado():
    return {
        "httpMethod": "POST",
        "resource": "/lancamentos",
        "headers": {
            "Authorization": f"Bearer {TOKEN}",
            "X-Api-Key": "chave-secreta-do-cliente",
            "Cookie": "sessao=abc123",
            "Content-Type": "application/json",
            "X-Correlation-Id": "req-abc-123",
        },
        "body": json.dumps({"tipo": "CREDITO", "valor": "100.00", "descricao": "Venda"}),
        "requestContext": {
            "requestId": "id-do-api-gateway",
            "identity": {"sourceIp": "200.1.2.3"},
            "authorizer": {"comercianteId": "COM#4711", "subject": "pdv-loja-001"},
        },
    }


class TestCredenciaisNuncaVazam:
    @pytest.mark.parametrize("segredo", [
        TOKEN,
        "chave-secreta-do-cliente",
        "sessao=abc123",
    ])
    def test_valor_sensivel_nao_aparece_no_log(self, evento_autenticado, segredo):
        assert segredo not in resumir_evento(evento_autenticado)

    def test_nem_um_prefixo_do_token_aparece(self, evento_autenticado):
        """Registrar os primeiros caracteres do token também é vazamento: reduz
        o espaço de busca de quem tentar forjá-lo."""
        registro = resumir_evento(evento_autenticado)

        assert TOKEN[:12] not in registro
        assert "Bearer" not in registro

    def test_corpo_da_requisicao_nao_e_registrado(self, evento_autenticado):
        """O corpo carrega dado de negócio e pode conter dado pessoal (LGPD).
        Só o tamanho é registrado."""
        registro = resumir_evento(evento_autenticado)

        assert "Venda" not in registro
        assert "100.00" not in registro
        assert json.loads(registro)["tamanho_corpo"] > 0


class TestDiagnosticoPreservado:
    def test_registra_o_suficiente_para_investigar(self, evento_autenticado):
        registro = json.loads(resumir_evento(evento_autenticado))

        assert registro["metodo"] == "POST"
        assert registro["recurso"] == "/lancamentos"
        assert registro["request_id"] == "id-do-api-gateway"
        assert registro["correlation_id"] == "req-abc-123"
        assert registro["origem"] == "200.1.2.3"

    def test_indica_presenca_de_autenticacao_sem_expor_o_valor(self, evento_autenticado):
        assert json.loads(resumir_evento(evento_autenticado))["autenticado"] is True

    def test_identidade_autenticada_e_registrada(self, evento_autenticado):
        """O comerciante vem do authorizer, já validado — é atributo de negócio,
        não credencial. É o que permite auditar quem registrou cada lançamento."""
        registro = json.loads(resumir_evento(evento_autenticado))

        assert registro["comerciante_id"] == "COM#4711"
        assert registro["subject"] == "pdv-loja-001"

    def test_evento_sem_autenticacao_nao_quebra(self):
        registro = json.loads(resumir_evento({"httpMethod": "GET", "resource": "/health"}))

        assert registro["autenticado"] is False
        assert registro["tamanho_corpo"] == 0


class TestEventosDeFila:
    def test_registra_contagem_e_ids_sem_o_conteudo(self):
        evento = {"Records": [
            {"messageId": "msg-1", "body": json.dumps({"valor": 1500.50})},
            {"messageId": "msg-2", "body": json.dumps({"valor": 300.25})},
        ]}

        registro = json.loads(resumir_evento_sqs(evento))

        assert registro["mensagens"] == 2
        assert registro["message_ids"] == ["msg-1", "msg-2"]
        assert "1500.50" not in json.dumps(registro)

    def test_lote_grande_nao_infla_o_log(self):
        evento = {"Records": [{"messageId": f"msg-{i}"} for i in range(500)]}

        registro = json.loads(resumir_evento_sqs(evento))

        assert registro["mensagens"] == 500
        assert len(registro["message_ids"]) == 10
