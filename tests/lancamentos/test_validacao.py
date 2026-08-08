"""
Regras de validação do lançamento (`utils.validate_lancamento`).

Estes testes cobrem o defeito mais grave encontrado na revisão: `ValidationError`
era usada em 6 pontos sem nunca ter sido definida, então TODA validação estourava
`NameError` e o cliente recebia "name 'ValidationError' is not defined" em vez da
mensagem de negócio.
"""
from decimal import Decimal

import pytest
from excecoes import ValidationError
from utils import validate_lancamento


class TestCamposObrigatorios:
    @pytest.mark.parametrize("campo", ["tipo", "valor", "descricao"])
    def test_campo_ausente_e_rejeitado(self, campo, lancamento_valido):
        payload = dict(lancamento_valido)
        del payload[campo]

        with pytest.raises(ValidationError) as erro:
            validate_lancamento(payload)

        # A mensagem precisa nomear o campo: é ela que chega ao cliente no HTTP 400.
        assert campo in str(erro.value)

    @pytest.mark.parametrize("vazio", ["", None, []])
    def test_campo_preenchido_com_valor_vazio_e_rejeitado(self, vazio, lancamento_valido):
        payload = dict(lancamento_valido, descricao=vazio)

        with pytest.raises(ValidationError):
            validate_lancamento(payload)


class TestTipo:
    @pytest.mark.parametrize("tipo", ["CREDITO", "DEBITO"])
    def test_tipos_do_dominio_sao_aceitos(self, tipo, lancamento_valido):
        resultado = validate_lancamento(dict(lancamento_valido, tipo=tipo))
        assert resultado["tipo"] == tipo

    @pytest.mark.parametrize("tipo", ["credito", "TRANSFERENCIA", "C", "ESTORNO"])
    def test_tipo_fora_do_dominio_e_rejeitado(self, tipo, lancamento_valido):
        with pytest.raises(ValidationError, match="DEBITO.*CREDITO|CREDITO.*DEBITO"):
            validate_lancamento(dict(lancamento_valido, tipo=tipo))


class TestValor:
    def test_valor_negativo_e_rejeitado(self, lancamento_valido):
        # O sinal do movimento vem do campo `tipo`, nunca do sinal do valor.
        # Aceitar valor negativo permitiria um "CREDITO de -100" que subtrai do saldo.
        with pytest.raises(ValidationError, match="maior que zero"):
            validate_lancamento(dict(lancamento_valido, valor=Decimal("-1")))

    def test_valor_zero_e_rejeitado(self, lancamento_valido):
        with pytest.raises(ValidationError, match="maior que zero"):
            validate_lancamento(dict(lancamento_valido, valor=Decimal("0.00")))

    def test_valor_nao_numerico_e_rejeitado(self, lancamento_valido):
        with pytest.raises(ValidationError):
            validate_lancamento(dict(lancamento_valido, valor="abc"))

    def test_valor_e_convertido_para_decimal_e_nao_float(self, lancamento_valido):
        """Dinheiro nunca deve trafegar como float: 0.1 não é representável em
        IEEE-754 e o erro acumula ao somar milhares de lançamentos."""
        resultado = validate_lancamento(dict(lancamento_valido, valor="1500.50"))

        assert isinstance(resultado["valor"], Decimal)
        assert resultado["valor"] == Decimal("1500.50")

    def test_valor_string_com_decimais_preserva_precisao(self, lancamento_valido):
        resultado = validate_lancamento(dict(lancamento_valido, valor="0.10"))
        assert resultado["valor"] == Decimal("0.10")


class TestDescricao:
    def test_descricao_no_limite_e_aceita(self, lancamento_valido):
        resultado = validate_lancamento(dict(lancamento_valido, descricao="x" * 255))
        assert len(resultado["descricao"]) == 255

    def test_descricao_acima_do_limite_e_rejeitada(self, lancamento_valido):
        with pytest.raises(ValidationError, match="255"):
            validate_lancamento(dict(lancamento_valido, descricao="x" * 256))

    def test_espacos_em_branco_sao_removidos(self, lancamento_valido):
        resultado = validate_lancamento(dict(lancamento_valido, descricao="  Venda  "))
        assert resultado["descricao"] == "Venda"

    def test_descricao_so_com_espacos_e_rejeitada(self, lancamento_valido):
        with pytest.raises(ValidationError):
            validate_lancamento(dict(lancamento_valido, descricao="   "))


class TestData:
    def test_data_iso_valida_e_preservada(self, lancamento_valido):
        resultado = validate_lancamento(
            dict(lancamento_valido, data="2025-07-30T14:30:00+00:00")
        )
        assert resultado["data"] == "2025-07-30T14:30:00+00:00"

    def test_sufixo_z_e_aceito(self, lancamento_valido):
        resultado = validate_lancamento(
            dict(lancamento_valido, data="2025-07-30T14:30:00Z")
        )
        assert resultado["data"] == "2025-07-30T14:30:00Z"

    def test_data_em_formato_invalido_e_rejeitada(self, lancamento_valido):
        with pytest.raises(ValidationError, match="ISO 8601"):
            validate_lancamento(dict(lancamento_valido, data="30/07/2025"))

    def test_data_ausente_recebe_o_instante_atual(self, lancamento_valido):
        resultado = validate_lancamento(lancamento_valido)
        assert resultado["data"]  # preenchida automaticamente


class TestValoresPadrao:
    def test_categoria_ausente_vira_geral(self, lancamento_valido):
        payload = dict(lancamento_valido)
        del payload["categoria"]
        assert validate_lancamento(payload)["categoria"] == "GERAL"

    def test_tags_ausentes_viram_lista_vazia(self, lancamento_valido):
        assert validate_lancamento(lancamento_valido)["tags"] == []
