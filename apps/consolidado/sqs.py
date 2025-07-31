import json
from typing import Dict, Any
from aws_xray_sdk.core import xray_recorder
from aws_xray_sdk.core import patch_all
from datetime import datetime, timezone
from operacoes import get_saldo_anterior, calculate_saldo_diario, save_saldo_diario, recalculate_subsequent_balances
from redis_ops import invalidate_cache
from configuration import Config

patch_all()
config = Config()

@xray_recorder.capture('handle_sqs_event')
def handle_sqs_event(event: Dict[str, Any]) -> None:
    """
    Manipula eventos do SQS
    """
    records = event.get('Records', [])

    for record in records:
        try:
            # Extrair corpo da mensagem
            body = record.get('body', '{}')
            if isinstance(body, str):
                message = json.loads(body)
            else:
                message = body

            # Processar mensagem
            process_sqs_message(message)

            config.logger.info(f"Mensagem SQS processada com sucesso: {record.get('messageId')}")

        except Exception as e:
            config.logger.error(f"Erro ao processar registro SQS: {str(e)}")
            # Relançar exceção para que a mensagem seja enviada para DLQ
            raise


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
        #raise ConsolidadoError(f"Erro no processamento: {str(e)}")
        raise f"Erro no processamento: {str(e)}"