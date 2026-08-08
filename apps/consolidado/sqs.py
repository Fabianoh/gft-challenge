import json
from typing import Dict, Any, List
from aws_xray_sdk.core import xray_recorder
from aws_xray_sdk.core import patch_all
from datetime import datetime, timezone
from operacoes import get_saldo_anterior, calculate_saldo_diario, save_saldo_diario, recalculate_subsequent_balances
from redis_ops import invalidate_cache
from configuration import Config
from excecoes import ProcessamentoError

patch_all()
config = Config()

@xray_recorder.capture('handle_sqs_event')
def handle_sqs_event(event: Dict[str, Any]) -> List[str]:
    """Processa o lote e devolve os identificadores das mensagens que falharam.

    Antes esta função relançava a exceção no primeiro erro, o que fazia o lote
    INTEIRO voltar para a fila — inclusive as mensagens já processadas com
    sucesso, que seriam processadas de novo. Com a agregação incremental do
    ADR-004 isso significaria contabilizar valores em dobro.

    Agora cada mensagem é isolada: a que falha volta sozinha para a fila e, após
    `maxReceiveCount`, cai na DLQ. As demais são confirmadas. Isso depende de
    `FunctionResponseTypes: [ReportBatchItemFailures]` estar habilitado no event
    source mapping — as duas pontas precisam estar de acordo.
    """
    falhas: List[str] = []

    for record in event.get('Records', []):
        identificador = record.get('messageId')
        try:
            body = record.get('body', '{}')
            message = json.loads(body) if isinstance(body, str) else body

            process_sqs_message(message)

            config.logger.info(f"Mensagem SQS processada com sucesso: {identificador}")

        except Exception as e:
            # Não relançar: relançar derrubaria o lote inteiro. Registrar a
            # falha desta mensagem preserva o progresso das demais.
            config.logger.error(
                f"Erro ao processar registro SQS {identificador}: {e}", exc_info=True
            )
            falhas.append(identificador)

    return falhas


@xray_recorder.capture('process_sqs_message')
def process_sqs_message(message: Dict[str, Any]) -> None:
    try:
        # Extrair dados da mensagem
        event_type = message.get('eventType')
        lancamento_data = message.get('data', message.get('timestamp', datetime.now(timezone.utc).isoformat()))

        if event_type != 'LANCAMENTO_CRIADO':
            config.logger.warning(f"Tipo de evento não suportado: {event_type}")
            return

        # Extrair data do lançamento
        data_lancamento = lancamento_data.split('T')[0]

        # Recalcular saldo para a data
        saldo_anterior = get_saldo_anterior(data_lancamento)
        saldo_diario = calculate_saldo_diario(data_lancamento, saldo_anterior)

        # Salvar saldo atualizado
        save_saldo_diario(saldo_diario)

        # Invalidar cache para a data
        invalidate_cache(f"saldo_diario:{data_lancamento}:*")
        invalidate_cache(f"relatorio:*")

        # Recalcular saldos dos dias seguintes (se necessário)
        recalculate_subsequent_balances(data_lancamento)

        config.logger.info(f"Consolidado atualizado para {data_lancamento}")

    except Exception as e:
        config.logger.error(f"Erro ao processar mensagem SQS: {str(e)}")
        raise ProcessamentoError(f"Erro no processamento: {e}") from e