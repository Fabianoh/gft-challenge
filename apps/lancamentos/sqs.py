from configuration import Config
import json
from datetime import datetime, timezone
from typing import Dict, Any
from aws_xray_sdk.core import xray_recorder
from aws_xray_sdk.core import patch_all

patch_all()

config = Config()


@xray_recorder.capture('send_to_consolidacao_queue')
def send_to_consolidacao_queue(lancamento_id: str, lancamento_data: Dict[str, Any]) -> None:
    try:
        # Preparar mensagem
        message = {
            'eventType': 'LANCAMENTO_CRIADO',
            'lancamentoId': lancamento_id,
            'tipo': lancamento_data['tipo'],
            'valor': float(lancamento_data['valor']),
            'data': lancamento_data['data'],
            'categoria': lancamento_data['categoria'],
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'ambiente': config.environment
        }

        # Enviar para SQS
        response = config.sqsClient.send_message(
            QueueUrl=config.SQS_QUEUE_URL,
            MessageBody=json.dumps(message, default=str),
            MessageAttributes={
                'EventType': {
                    'StringValue': 'LANCAMENTO_CRIADO',
                    'DataType': 'String'
                },
                'LancamentoId': {
                    'StringValue': lancamento_id,
                    'DataType': 'String'
                }
            }
        )

        config.logger.info(f"Mensagem enviada para SQS: {response['MessageId']}")

    except BaseException as e:
        config.logger.error(f"Erro ao enviar para SQS: {e}")