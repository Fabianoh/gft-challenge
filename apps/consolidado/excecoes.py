"""
Exceções de domínio do serviço de Consolidado.

Ver `apps/lancamentos/excecoes.py` para a justificativa.
"""


class ConsolidadoError(Exception):
    """Erro base do domínio de consolidação."""


class PersistenciaError(ConsolidadoError):
    """Falha ao ler ou gravar no armazenamento."""


class ProcessamentoError(ConsolidadoError):
    """Falha ao processar um evento de lançamento.

    Propagada de propósito no consumidor SQS: a mensagem precisa voltar para a
    fila e, após `maxReceiveCount`, cair na DLQ. Engolir esta exceção faria o
    lançamento nunca ser consolidado, silenciosamente.
    """
