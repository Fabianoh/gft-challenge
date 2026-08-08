"""
Regra de negócio central do desafio: o saldo diário consolidado.

O saldo de um dia é `saldo_anterior + créditos_do_dia − débitos_do_dia`.
É a partir daqui que sai o relatório que o comerciante usa para fechar o caixa,
então precisão e tratamento de casos de borda importam mais do que performance.
"""
from decimal import Decimal

import pytest
from operacoes import calculate_saldo_diario, get_saldo_anterior, save_saldo_diario


class TestCalculoDoSaldoDiario:
    def test_somente_creditos(self, gravar_lancamento):
        gravar_lancamento("CREDITO", "100.00")
        gravar_lancamento("CREDITO", "250.50")

        saldo = calculate_saldo_diario("2025-07-30")

        assert saldo.total_creditos == Decimal("350.50")
        assert saldo.total_debitos == Decimal("0")
        assert saldo.saldo_final == Decimal("350.50")

    def test_somente_debitos_gera_saldo_negativo(self, gravar_lancamento):
        """Saldo negativo é um estado de negócio legítimo: o comerciante pode
        gastar mais do que recebeu no dia. O sistema não deve impedir nem zerar."""
        gravar_lancamento("DEBITO", "80.00")

        saldo = calculate_saldo_diario("2025-07-30")

        assert saldo.saldo_final == Decimal("-80.00")

    def test_creditos_e_debitos_no_mesmo_dia(self, gravar_lancamento):
        gravar_lancamento("CREDITO", "1000.00")
        gravar_lancamento("DEBITO", "300.00")
        gravar_lancamento("DEBITO", "150.25")

        saldo = calculate_saldo_diario("2025-07-30")

        assert saldo.total_creditos == Decimal("1000.00")
        assert saldo.total_debitos == Decimal("450.25")
        assert saldo.saldo_final == Decimal("549.75")

    def test_saldo_anterior_e_transportado(self, gravar_lancamento):
        """O saldo de abertura do dia é o fechamento do dia anterior — é isso que
        torna o relatório uma série contínua e não fotos isoladas."""
        gravar_lancamento("CREDITO", "100.00")

        saldo = calculate_saldo_diario("2025-07-30", saldo_anterior=Decimal("500.00"))

        assert saldo.saldo_inicial == Decimal("500.00")
        assert saldo.saldo_final == Decimal("600.00")

    def test_dia_sem_movimento_preserva_o_saldo_anterior(self):
        saldo = calculate_saldo_diario("2025-07-30", saldo_anterior=Decimal("742.19"))

        assert saldo.quantidade_lancamentos == 0
        assert saldo.total_creditos == Decimal("0")
        assert saldo.saldo_final == Decimal("742.19")

    def test_quantidade_de_lancamentos_e_contabilizada(self, gravar_lancamento):
        for _ in range(3):
            gravar_lancamento("CREDITO", "10.00")

        assert calculate_saldo_diario("2025-07-30").quantidade_lancamentos == 3

    def test_saldo_anterior_aceita_valor_nao_decimal(self, gravar_lancamento):
        """Blindagem contra chamador que passe int/float/str por engano."""
        gravar_lancamento("CREDITO", "50.00")

        saldo = calculate_saldo_diario("2025-07-30", saldo_anterior=100)

        assert saldo.saldo_final == Decimal("150.00")


class TestIsolamentoEntreDias:
    def test_lancamento_de_outro_dia_nao_entra_no_saldo(self, gravar_lancamento):
        gravar_lancamento("CREDITO", "100.00", data="2025-07-30T10:00:00")
        gravar_lancamento("CREDITO", "999.00", data="2025-07-31T10:00:00")

        saldo = calculate_saldo_diario("2025-07-30")

        assert saldo.total_creditos == Decimal("100.00")

    def test_lancamento_a_meia_noite_pertence_ao_dia_que_comeca(self, gravar_lancamento):
        gravar_lancamento("CREDITO", "10.00", data="2025-07-30T00:00:00")

        assert calculate_saldo_diario("2025-07-30").quantidade_lancamentos == 1

    def test_lancamento_no_ultimo_segundo_ainda_pertence_ao_dia(self, gravar_lancamento):
        gravar_lancamento("CREDITO", "10.00", data="2025-07-30T23:59:59")

        assert calculate_saldo_diario("2025-07-30").quantidade_lancamentos == 1


class TestLancamentosCancelados:
    def test_lancamento_nao_ativo_e_ignorado(self, gravar_lancamento):
        """Cancelamento é feito por mudança de status (soft delete), preservando
        a trilha de auditoria. O saldo só considera lançamentos ATIVOs."""
        gravar_lancamento("CREDITO", "100.00", status="ATIVO")
        gravar_lancamento("CREDITO", "500.00", status="CANCELADO")

        saldo = calculate_saldo_diario("2025-07-30")

        assert saldo.total_creditos == Decimal("100.00")
        assert saldo.quantidade_lancamentos == 1


class TestPrecisaoMonetaria:
    def test_soma_de_centavos_nao_acumula_erro(self, gravar_lancamento):
        """Com float, 0.1 somado 10 vezes dá 0.9999999999999999.
        Com Decimal, dá exatamente 1.00 — que é o que o caixa precisa fechar."""
        for _ in range(10):
            gravar_lancamento("CREDITO", "0.10")

        saldo = calculate_saldo_diario("2025-07-30")

        assert saldo.total_creditos == Decimal("1.00")
        assert isinstance(saldo.total_creditos, Decimal)

    def test_valores_com_muitas_casas_sao_preservados(self, gravar_lancamento):
        gravar_lancamento("CREDITO", "1234567.89")
        gravar_lancamento("DEBITO", "0.01")

        saldo = calculate_saldo_diario("2025-07-30")

        assert saldo.saldo_final == Decimal("1234567.88")


class TestSaldoAnterior:
    def test_sem_dia_anterior_o_saldo_inicial_e_zero(self):
        assert get_saldo_anterior("2025-07-30") == Decimal("0")

    def test_saldo_anterior_vem_do_fechamento_do_dia_anterior(self, gravar_lancamento):
        gravar_lancamento("CREDITO", "400.00", data="2025-07-29T10:00:00")
        save_saldo_diario(calculate_saldo_diario("2025-07-29"))

        assert get_saldo_anterior("2025-07-30") == Decimal("400.00")


class TestPersistencia:
    def test_saldo_e_gravado_na_tabela_de_consolidado(self, gravar_lancamento, tabela_consolidado):
        gravar_lancamento("CREDITO", "100.00")
        gravar_lancamento("DEBITO", "25.00")

        save_saldo_diario(calculate_saldo_diario("2025-07-30"))

        item = tabela_consolidado.get_item(Key={"data": "2025-07-30"})["Item"]
        assert item["saldo_final"] == Decimal("75.00")
        assert item["quantidade_lancamentos"] == 2

    def test_regravar_o_mesmo_dia_substitui_e_nao_duplica(self, gravar_lancamento, tabela_consolidado):
        """A chave da tabela é a data, então reprocessar um dia sobrescreve.
        É essa propriedade que torna o recálculo idempotente hoje."""
        gravar_lancamento("CREDITO", "100.00")
        save_saldo_diario(calculate_saldo_diario("2025-07-30"))
        save_saldo_diario(calculate_saldo_diario("2025-07-30"))

        assert tabela_consolidado.scan()["Count"] == 1
