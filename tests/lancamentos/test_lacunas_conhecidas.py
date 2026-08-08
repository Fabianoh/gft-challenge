"""
Lacunas conhecidas do MVP, já cobertas por teste.

Cada teste aqui descreve um comportamento que a arquitetura alvo exige e que a
implementação atual ainda não entrega. Todos usam `xfail(strict=True)`: enquanto
o defeito existe, o teste é reportado como XFAIL (esperado); no instante em que a
correção entrar, ele passa e o pytest acusa XPASS como FALHA — obrigando a
remover o marcador. Isso impede que uma lacuna seja "esquecida como resolvida".

Referência das correções: docs/adr/ e ANALISE-E-PLANO.md (P1).
"""
import json
from decimal import Decimal

import pytest
from main import lambda_handler


def corpo(resposta):
    return json.loads(resposta["body"])


@pytest.mark.xfail(
    strict=True,
    reason="P1 — sem idempotência: retry do cliente duplica o lançamento. "
           "Correção: header Idempotency-Key + escrita condicional.",
)
def test_repeticao_com_mesma_chave_de_idempotencia_nao_duplica(evento_post, lancamento_valido, tabela):
    """Um POST repetido (timeout de rede, retry de app mobile) não pode gerar um
    segundo lançamento: seria dinheiro duplicado no caixa do comerciante."""
    evento = evento_post(lancamento_valido)
    evento["headers"] = {"Idempotency-Key": "chave-fixa-do-cliente"}

    lambda_handler(evento, None)
    lambda_handler(evento, None)

    assert tabela.scan()["Count"] == 1


@pytest.mark.xfail(
    strict=True,
    reason="P1 — dual-write: falha no SQS é engolida e a API responde 201. "
           "Correção: publicar o evento via DynamoDB Streams (outbox transacional).",
)
def test_falha_ao_publicar_evento_nao_pode_responder_sucesso(
    evento_post, lancamento_valido, monkeypatch
):
    """Hoje o lançamento é gravado, o envio para a fila falha, a exceção é
    engolida e o cliente recebe 201. O lançamento existe e nunca será
    consolidado — o saldo fica errado para sempre, sem nenhum sinal."""
    import sqs as modulo_sqs

    def falha(*_args, **_kwargs):
        raise RuntimeError("SQS indisponível")

    monkeypatch.setattr(modulo_sqs.config.sqsClient, "send_message", falha)

    resposta = lambda_handler(evento_post(lancamento_valido), None)

    assert resposta["statusCode"] >= 500, (
        "responder 201 depois de perder o evento de consolidação é perda "
        "silenciosa de dados"
    )


@pytest.mark.xfail(
    strict=True,
    reason="P1 — dinheiro serializado como float na resposta. "
           "Correção: manter Decimal e serializar como string.",
)
def test_valor_na_resposta_preserva_precisao_decimal(evento_post, lancamento_valido):
    """`float` IEEE-754 não representa 0.1 exatamente. Somando milhares de
    lançamentos o erro acumula e o saldo deixa de fechar no centavo."""
    resposta = lambda_handler(evento_post(dict(lancamento_valido, valor="0.10")), None)

    valor = corpo(resposta)["data"]["valor"]

    assert isinstance(valor, str), "valor monetário deve trafegar como string"
    assert Decimal(valor) == Decimal("0.10")
