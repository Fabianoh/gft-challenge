"""
Lacunas conhecidas do MVP no domínio de Consolidação.

Ver `tests/lancamentos/test_lacunas_conhecidas.py` para a explicação do
`xfail(strict=True)`: enquanto o defeito existe o teste é XFAIL; quando a
correção entrar ele passa e o pytest acusa XPASS como falha, forçando a
atualização do marcador.
"""
from decimal import Decimal

import pytest
from main import lambda_handler
from operacoes import calculate_saldo_diario


@pytest.mark.xfail(
    strict=True,
    reason="P1 — o dia de negócio é fatiado em UTC. Correção: usar o fuso do "
           "comerciante (America/Sao_Paulo) e um campo data_movimento próprio.",
)
def test_venda_da_noite_pertence_ao_dia_local_do_comerciante(gravar_lancamento):
    """Uma venda às 22h de 30/07 em São Paulo é gravada como 31/07T01:00Z.
    Hoje ela entra no saldo do dia 31 — o comerciante fecha o caixa do dia 30 e
    o relatório não bate com o que ele contou na gaveta.

    "Saldo diário" é conceito do domínio de negócio, preso ao calendário local
    do comerciante, não ao relógio UTC do servidor."""
    gravar_lancamento("CREDITO", "150.00", data="2025-07-31T01:00:00")

    saldo = calculate_saldo_diario("2025-07-30")

    assert saldo.total_creditos == Decimal("150.00")


@pytest.mark.xfail(
    strict=True,
    reason="P1 — recálculo por varredura: um evento dispara dezenas de scans. "
           "Correção: agregação incremental com UpdateItem/ADD atômico (ADR-004).",
)
def test_um_evento_nao_pode_disparar_varredura_da_tabela(
    mensagem_sqs, gravar_lancamento, monkeypatch
):
    """Este é o teste que traduz o requisito não-funcional de 50 req/s em
    verificação automática.

    Hoje cada mensagem executa `calculate_saldo_diario` (1 scan) e em seguida
    `recalculate_subsequent_balances`, que percorre até 30 dias fazendo 2 scans
    por dia. A 50 rps isso são milhares de varreduras por segundo sobre a tabela
    de lançamentos — o Lambda estoura o timeout de 60s e a mensagem cai na DLQ.

    A arquitetura alvo aplica o delta que já veio dentro da mensagem, sem
    reler a origem: nenhum scan."""
    import operacoes

    gravar_lancamento("CREDITO", "100.00", data="2025-07-30T10:00:00")

    scan_original = operacoes.config.tableLancamentos.scan
    varreduras = []

    def scan_contabilizado(**kwargs):
        varreduras.append(kwargs)
        return scan_original(**kwargs)

    monkeypatch.setattr(operacoes.config.tableLancamentos, "scan", scan_contabilizado)

    lambda_handler(mensagem_sqs(data="2025-07-30T10:00:00"), None)

    assert len(varreduras) == 0, (
        f"{len(varreduras)} varreduras da tabela de lançamentos para um único "
        "evento; a 50 rps isso multiplica por 50 a cada segundo"
    )


@pytest.mark.xfail(
    strict=True,
    reason="P1 — o consolidado lê a tabela do domínio de Lançamentos. "
           "Correção: store próprio alimentado só por eventos.",
)
def test_consolidado_nao_deve_acessar_a_tabela_de_lancamentos(
    mensagem_sqs, gravar_lancamento, monkeypatch
):
    """Banco compartilhado entre bounded contexts: as leituras do consolidado
    competem por capacidade na mesma tabela em que os lançamentos escrevem.
    Um pico no consolidado pode causar throttling na escrita — exatamente o
    cenário que o requisito não-funcional principal proíbe."""
    import operacoes

    gravar_lancamento("CREDITO", "100.00", data="2025-07-30T10:00:00")

    acessos = []
    for metodo in ("scan", "query", "get_item"):
        original = getattr(operacoes.config.tableLancamentos, metodo)

        def registrar(*args, _m=metodo, _o=original, **kwargs):
            acessos.append(_m)
            return _o(*args, **kwargs)

        monkeypatch.setattr(operacoes.config.tableLancamentos, metodo, registrar)

    lambda_handler(mensagem_sqs(data="2025-07-30T10:00:00"), None)

    assert acessos == [], f"acoplamento no nível de dados: {set(acessos)}"
