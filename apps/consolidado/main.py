import json
from typing import Dict, Any
from aws_xray_sdk.core import xray_recorder
from aws_xray_sdk.core import patch_all
from configuration import Config
from sqs import handle_sqs_event
from utils import create_response, route_http_request

patch_all()
config = Config()


@xray_recorder.capture('lambda_handler')
def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    try:
        # Log da requisição
        config.logger.info(f"Evento recebido: {json.dumps(event, default=str)}")

        # Verificar tipo de evento
        if 'Records' in event:
            # Evento do SQS
            handle_sqs_event(event)
            return {'statusCode': 200, 'body': 'SQS events processed successfully'}

        elif 'httpMethod' in event:
            # Requisição HTTP do API Gateway
            return route_http_request(event)

        else:
            # Evento direto (para testes)
            return create_response(400, {
                'error': 'Tipo de evento não suportado',
                'message': 'Esta Lambda suporta apenas eventos SQS e HTTP'
            })

    except BaseException as e:
        config.logger.error(f"Erro inesperado no handler: {str(e)}", exc_info=True)
        if 'Records' in event:
            # Para eventos SQS, relançar exceção
            raise
        else:
            # Para HTTP, retornar erro
            return create_response(500, {
                'error': 'Erro interno do servidor',
                'message': 'Ocorreu um erro inesperado'
            })
