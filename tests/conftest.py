"""
Guarda de execução das suítes.

Lançamentos e consolidado são unidades de deploy independentes: cada uma é
empacotada com seus módulos na raiz do .zip do Lambda e tem arquivos de mesmo
nome (`main.py`, `utils.py`, `operacoes.py`, `sqs.py`, `configuration.py`).

Carregar as duas no mesmo processo Python faria uma sobrescrever a outra em
`sys.modules` — uma colisão que não existe em produção, onde cada função roda
isolada. Em vez de deixar isso falhar com um erro obscuro de tabela duplicada,
falhamos aqui com a instrução correta.
"""
from pathlib import Path

import pytest

SERVICOS = ("lancamentos", "consolidado", "authorizer")


def pytest_configure(config):
    alvos = [Path(arg.split("::")[0]).resolve() for arg in config.args]
    raiz_testes = Path(__file__).parent.resolve()

    selecionados = set()
    for servico in SERVICOS:
        diretorio = raiz_testes / servico
        for alvo in alvos:
            if alvo == diretorio or diretorio in alvo.parents or alvo in (raiz_testes, raiz_testes.parent):
                selecionados.add(servico)

    if len(selecionados) > 1:
        raise pytest.UsageError(
            "as suítes de serviços diferentes não podem rodar no mesmo processo: "
            "são unidades de deploy independentes com módulos de mesmo nome. "
            "Use `python tarefas.py testar`, ou rode uma por vez: "
            "`pytest tests/lancamentos`, `pytest tests/consolidado`, "
            "`pytest tests/authorizer`."
        )
