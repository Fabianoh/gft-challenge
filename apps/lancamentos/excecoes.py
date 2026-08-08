"""
Exceções de domínio do serviço de Lançamentos.

Motivo: o código original usava `raise "mensagem"`, que em Python levanta
`TypeError: exceptions must derive from BaseException` e descarta a causa raiz.
Exceções tipadas permitem tratar cada falha na camada correta e preservar o
encadeamento com `raise ... from e`.
"""


class LancamentoError(Exception):
    """Erro base do domínio de lançamentos."""


class ValidationError(LancamentoError):
    """Payload do lançamento violou uma regra de negócio.

    Traduzido para HTTP 400 pela camada de operações.
    """


class PersistenciaError(LancamentoError):
    """Falha ao ler ou gravar no armazenamento.

    Traduzido para HTTP 500 — é uma falha de infraestrutura, não do cliente.
    """
