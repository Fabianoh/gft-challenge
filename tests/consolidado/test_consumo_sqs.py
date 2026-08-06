"""
Consumo assíncrono da fila de consolidação.

Este é o ponto que sustenta o requisito não-funcional principal do desafio:
"o serviço de lançamento não deve ficar indisponível se o consolidado cair".
O desacoplamento só funciona se o consumidor tratar corretamente falha,
reprocessamento e evento desconhecido.
"""
from decimal import Decimal

import pytest
from excecoes import ProcessamentoError
from main import lambda_handler


class TestProcessamentoDeEvento:
    def test_evento_de_lancamento_atualiza_o_consolidado(
        self, mensagem_sqs, gravar_lancamento, tabela_consolidado
    ):
        gravar_lancamento("CREDITO", "250.00", data="2025-07-30T10:00:00")

        resposta = lambda_handler(mensagem_sqs(data="2025-07-30T10:00:00"), None)

        assert resposta["batchItemFailures"] == []
        item = tabela_consolidado.get_item(Key={"data": "2025-07-30"})["Item"]
        assert item["saldo_final"] == Decimal("250.00")

    def test_evento_de_tipo_desconhecido_e_ignorado_sem_erro(
        self, mensagem_sqs, tabela_consolidado
    ):
        """Um evento que este serviço não entende não pode derrubar o batch nem
        ir para a DLQ: ele simplesmente não é do interesse deste consumidor."""
        resposta = lambda_handler(mensagem_sqs(evento="LANCAMENTO_ESTORNADO"), None)

        assert resposta["batchItemFailures"] == []
        assert tabela_consolidado.scan()["Count"] == 0

    def test_reprocessar_a_mesma_mensagem_nao_altera_o_saldo(
        self, mensagem_sqs, gravar_lancamento, tabela_consolidado
    ):
        """SQS Standard entrega ao menos uma vez, então reentrega é rotina.
        Hoje o saldo é recalculado a partir da fonte, o que torna o
        reprocessamento naturalmente idempotente.

        Atenção ao evoluir: ao migrar para agregação incremental com `ADD`
        atômico (ADR-004), esta propriedade se perde e passa a exigir
        deduplicação explícita por evento_id. Este teste é a rede de segurança
        dessa migração."""
        gravar_lancamento("CREDITO", "100.00", data="2025-07-30T10:00:00")
        evento = mensagem_sqs(data="2025-07-30T10:00:00")

        lambda_handler(evento, None)
        primeiro = tabela_consolidado.get_item(Key={"data": "2025-07-30"})["Item"]["saldo_final"]

        lambda_handler(evento, None)
        segundo = tabela_consolidado.get_item(Key={"data": "2025-07-30"})["Item"]["saldo_final"]

        assert primeiro == segundo == Decimal("100.00")

    def test_varios_lancamentos_do_dia_sao_somados(
        self, mensagem_sqs, gravar_lancamento, tabela_consolidado
    ):
        gravar_lancamento("CREDITO", "300.00", data="2025-07-30T09:00:00")
        gravar_lancamento("DEBITO", "120.00", data="2025-07-30T15:00:00")

        lambda_handler(mensagem_sqs(data="2025-07-30T15:00:00"), None)

        item = tabela_consolidado.get_item(Key={"data": "2025-07-30"})["Item"]
        assert item["total_creditos"] == Decimal("300.00")
        assert item["total_debitos"] == Decimal("120.00")
        assert item["saldo_final"] == Decimal("180.00")


class TestTratamentoDeFalha:
    def test_mensagem_que_falha_e_devolvida_para_a_fila(self, mensagem_sqs, monkeypatch):
        """A mensagem precisa voltar para a fila e, após `maxReceiveCount`, cair
        na DLQ. Se a falha fosse engolida, a mensagem sairia da fila como se
        tivesse sido processada e o lançamento nunca entraria no saldo — perda
        silenciosa, o pior modo de falha em domínio financeiro."""
        import sqs as modulo_sqs

        def falha(*_args, **_kwargs):
            raise RuntimeError("DynamoDB indisponível")

        # `sqs.py` faz `from operacoes import calculate_saldo_diario`, então o
        # nome está ligado ao namespace de `sqs` — é lá que o mock precisa entrar.
        monkeypatch.setattr(modulo_sqs, "calculate_saldo_diario", falha)

        resposta = lambda_handler(mensagem_sqs(id_lancamento="lanc-0007"), None)

        assert resposta["batchItemFailures"] == [{"itemIdentifier": "msg-lanc-0007"}]

    def test_falha_em_uma_mensagem_nao_derruba_as_demais(
        self, gravar_lancamento, tabela_consolidado, monkeypatch
    ):
        """Esta é a razão de existir das respostas parciais de lote.

        Sem elas, uma falha na última mensagem devolveria as dez à fila e as nove
        já aplicadas seriam processadas de novo — o que, com agregação
        incremental (ADR-004), significa contabilizar valores em dobro."""
        import json

        import sqs as modulo_sqs

        gravar_lancamento("CREDITO", "100.00", data="2025-07-30T10:00:00")

        original = modulo_sqs.calculate_saldo_diario
        chamadas = {"n": 0}

        def falha_apenas_na_segunda(*args, **kwargs):
            chamadas["n"] += 1
            if chamadas["n"] == 2:
                raise RuntimeError("falha transitória")
            return original(*args, **kwargs)

        monkeypatch.setattr(modulo_sqs, "calculate_saldo_diario", falha_apenas_na_segunda)

        corpo = json.dumps({"eventType": "LANCAMENTO_CRIADO", "data": "2025-07-30T10:00:00"})
        evento = {"Records": [
            {"messageId": "msg-ok-1", "body": corpo},
            {"messageId": "msg-falha", "body": corpo},
            {"messageId": "msg-ok-2", "body": corpo},
        ]}

        resposta = lambda_handler(evento, None)

        assert resposta["batchItemFailures"] == [{"itemIdentifier": "msg-falha"}]
        # As duas bem-sucedidas foram efetivamente aplicadas
        assert tabela_consolidado.get_item(Key={"data": "2025-07-30"})["Item"]["saldo_final"] \
            == Decimal("100.00")

    def test_erro_de_processamento_usa_excecao_tipada(self, monkeypatch):
        """Regressão: o código original fazia `raise f"..."`, que levanta
        TypeError e descarta a causa raiz justamente no momento do diagnóstico."""
        import sqs as modulo_sqs

        def falha(*_args, **_kwargs):
            raise RuntimeError("falha na origem")

        monkeypatch.setattr(modulo_sqs, "get_saldo_anterior", falha)

        with pytest.raises(ProcessamentoError) as erro:
            modulo_sqs.process_sqs_message(
                {"eventType": "LANCAMENTO_CRIADO", "data": "2025-07-30T10:00:00"}
            )

        assert erro.value.__cause__ is not None, "a causa raiz precisa ser preservada"


class TestRoteamentoDoHandler:
    def test_evento_sem_formato_reconhecido_retorna_400(self):
        resposta = lambda_handler({"foo": "bar"}, None)

        assert resposta["statusCode"] == 400

    def test_batch_com_varias_mensagens_processa_todas(
        self, gravar_lancamento, tabela_consolidado
    ):
        import json

        gravar_lancamento("CREDITO", "10.00", data="2025-07-29T10:00:00")
        gravar_lancamento("CREDITO", "20.00", data="2025-07-30T10:00:00")

        evento = {"Records": [
            {"messageId": "m1", "body": json.dumps(
                {"eventType": "LANCAMENTO_CRIADO", "data": "2025-07-29T10:00:00"})},
            {"messageId": "m2", "body": json.dumps(
                {"eventType": "LANCAMENTO_CRIADO", "data": "2025-07-30T10:00:00"})},
        ]}

        lambda_handler(evento, None)

        assert tabela_consolidado.get_item(Key={"data": "2025-07-29"})["Item"]["saldo_final"] == Decimal("10.00")
        # o dia 30 acumula o saldo de abertura vindo do dia 29
        assert tabela_consolidado.get_item(Key={"data": "2025-07-30"})["Item"]["saldo_final"] == Decimal("30.00")
