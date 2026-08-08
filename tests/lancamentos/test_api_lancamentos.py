"""
Testes de integração do serviço de Lançamentos.

Exercitam o `lambda_handler` de ponta a ponta contra DynamoDB, SQS e EventBridge
simulados em memória pelo moto. Nenhuma conta AWS é necessária.
"""
import json
from decimal import Decimal

import pytest
from main import lambda_handler


def corpo(resposta):
    """Desserializa o body da resposta do API Gateway."""
    return json.loads(resposta["body"])


class TestCriacaoDeLancamento:
    def test_lancamento_valido_retorna_201(self, evento_post, lancamento_valido):
        resposta = lambda_handler(evento_post(lancamento_valido), None)

        assert resposta["statusCode"] == 201
        assert corpo(resposta)["success"] is True
        assert corpo(resposta)["data"]["tipo"] == "CREDITO"

    def test_lancamento_e_persistido_no_dynamodb(self, evento_post, lancamento_valido, tabela):
        resposta = lambda_handler(evento_post(lancamento_valido), None)
        identificador = corpo(resposta)["data"]["id"]

        item = tabela.get_item(Key={"id": identificador})["Item"]

        assert item["tipo"] == "CREDITO"
        assert item["valor"] == Decimal("1500.50")
        assert item["descricao"] == "Venda balcão"
        assert item["status"] == "ATIVO"

    def test_valor_e_gravado_como_decimal_no_banco(self, evento_post, lancamento_valido, tabela):
        """O DynamoDB deve receber Decimal. Se o valor virasse float em algum
        ponto do caminho, a precisão já estaria perdida na gravação."""
        resposta = lambda_handler(evento_post(dict(lancamento_valido, valor="0.10")), None)
        identificador = corpo(resposta)["data"]["id"]

        item = tabela.get_item(Key={"id": identificador})["Item"]

        assert isinstance(item["valor"], Decimal)
        assert item["valor"] == Decimal("0.10")

    def test_evento_e_publicado_na_fila_de_consolidacao(self, evento_post, lancamento_valido, fila):
        """O desacoplamento entre os dois serviços depende desta mensagem.
        Se ela não for publicada, o saldo do comerciante fica errado."""
        resposta = lambda_handler(evento_post(lancamento_valido), None)
        identificador = corpo(resposta)["data"]["id"]

        cliente, url = fila
        mensagens = cliente.receive_message(QueueUrl=url, MaxNumberOfMessages=10)

        assert "Messages" in mensagens, "nenhuma mensagem publicada na fila"
        publicada = json.loads(mensagens["Messages"][0]["Body"])
        assert publicada["eventType"] == "LANCAMENTO_CRIADO"
        assert publicada["lancamentoId"] == identificador

    def test_id_gerado_e_um_uuid(self, evento_post, lancamento_valido):
        import uuid

        resposta = lambda_handler(evento_post(lancamento_valido), None)

        uuid.UUID(corpo(resposta)["data"]["id"])  # levanta ValueError se inválido

    def test_cada_lancamento_recebe_id_distinto(self, evento_post, lancamento_valido):
        primeiro = corpo(lambda_handler(evento_post(lancamento_valido), None))["data"]["id"]
        segundo = corpo(lambda_handler(evento_post(lancamento_valido), None))["data"]["id"]

        assert primeiro != segundo


class TestErrosDeEntrada:
    def test_payload_invalido_retorna_400_com_mensagem_de_negocio(self, evento_post):
        """Regressão do defeito mais grave da revisão: `ValidationError` não
        existia, então este caso devolvia a mensagem interna
        "name 'ValidationError' is not defined" ao cliente."""
        resposta = lambda_handler(evento_post({"tipo": "INVALIDO", "valor": 10, "descricao": "x"}), None)

        assert resposta["statusCode"] == 400
        mensagem = corpo(resposta)["message"]
        assert "DEBITO" in mensagem and "CREDITO" in mensagem
        assert "ValidationError" not in mensagem, "erro interno vazando para o cliente"

    def test_valor_nao_numerico_retorna_400_e_nao_500(self, evento_post, lancamento_valido):
        """`Decimal("abc")` levanta InvalidOperation, que não é ValueError.
        Sem tratamento explícito isto virava HTTP 500 (falha do servidor) em vez
        de 400 (erro do cliente) — e disparava alarme de erro indevidamente."""
        resposta = lambda_handler(evento_post(dict(lancamento_valido, valor="abc")), None)

        assert resposta["statusCode"] == 400

    def test_body_que_nao_e_json_retorna_400(self):
        resposta = lambda_handler(
            {"httpMethod": "POST", "resource": "/lancamentos", "body": "isto não é json"}, None
        )

        assert resposta["statusCode"] == 400
        assert corpo(resposta)["error"] == "JSON inválido"

    def test_nenhum_lancamento_e_gravado_quando_payload_e_invalido(self, evento_post, tabela):
        lambda_handler(evento_post({"tipo": "INVALIDO", "valor": 1, "descricao": "x"}), None)

        assert tabela.scan()["Count"] == 0


class TestConsultas:
    def test_busca_por_id_retorna_o_lancamento(self, evento_post, evento_get, lancamento_valido):
        criado = corpo(lambda_handler(evento_post(lancamento_valido), None))["data"]["id"]

        resposta = lambda_handler(
            evento_get("/lancamentos/{id}", path_params={"id": criado}), None
        )

        assert resposta["statusCode"] == 200
        assert corpo(resposta)["data"]["id"] == criado

    def test_id_inexistente_retorna_404(self, evento_get):
        resposta = lambda_handler(
            evento_get("/lancamentos/{id}",
                       path_params={"id": "550e8400-e29b-41d4-a716-446655440000"}),
            None,
        )

        assert resposta["statusCode"] == 404

    def test_id_malformado_retorna_400(self, evento_get):
        resposta = lambda_handler(
            evento_get("/lancamentos/{id}", path_params={"id": "123"}), None
        )

        assert resposta["statusCode"] == 400

    def test_listagem_traz_resumo_financeiro(self, evento_post, evento_get, lancamento_valido):
        lambda_handler(evento_post(dict(lancamento_valido, tipo="CREDITO", valor="100.00")), None)
        lambda_handler(evento_post(dict(lancamento_valido, tipo="DEBITO", valor="30.00")), None)

        resposta = lambda_handler(evento_get("/lancamentos"), None)

        resumo = corpo(resposta)["data"]["summary"]
        assert resumo["total_creditos"] == 100.0
        assert resumo["total_debitos"] == 30.0
        assert resumo["saldo_liquido"] == 70.0
        assert resumo["quantidade_total"] == 2

    def test_filtro_por_tipo(self, evento_post, evento_get, lancamento_valido):
        lambda_handler(evento_post(dict(lancamento_valido, tipo="CREDITO")), None)
        lambda_handler(evento_post(dict(lancamento_valido, tipo="DEBITO")), None)

        resposta = lambda_handler(evento_get("/lancamentos", query={"tipo": "DEBITO"}), None)

        lancamentos = corpo(resposta)["data"]["lancamentos"]
        assert len(lancamentos) == 1
        assert lancamentos[0]["tipo"] == "DEBITO"

    @pytest.mark.parametrize("limite", ["0", "101", "abc"])
    def test_limite_fora_da_faixa_retorna_400(self, evento_get, limite):
        resposta = lambda_handler(evento_get("/lancamentos", query={"limit": limite}), None)

        assert resposta["statusCode"] == 400

    def test_periodo_invertido_retorna_400(self, evento_get):
        resposta = lambda_handler(
            evento_get("/lancamentos",
                       query={"data_inicio": "2025-07-30", "data_fim": "2025-07-01"}),
            None,
        )

        assert resposta["statusCode"] == 400


class TestProtocoloHttp:
    def test_metodo_nao_suportado_retorna_405(self):
        resposta = lambda_handler(
            {"httpMethod": "DELETE", "resource": "/lancamentos", "body": "{}"}, None
        )

        assert resposta["statusCode"] == 405

    def test_preflight_cors_retorna_200(self):
        resposta = lambda_handler({"httpMethod": "OPTIONS", "resource": "/lancamentos"}, None)

        assert resposta["statusCode"] == 200

    def test_resposta_declara_content_type_json(self, evento_post, lancamento_valido):
        resposta = lambda_handler(evento_post(lancamento_valido), None)

        assert resposta["headers"]["Content-Type"] == "application/json"

    def test_acentuacao_e_preservada_no_json(self, evento_post, lancamento_valido):
        """`ensure_ascii=False` no create_response: a descrição precisa voltar
        legível, não como sequência de escapes unicode."""
        resposta = lambda_handler(
            evento_post(dict(lancamento_valido, descricao="Comissão de vendas")), None
        )

        assert "Comissão" in resposta["body"]
