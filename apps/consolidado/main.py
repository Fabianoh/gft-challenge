import json
from typing import Dict, Any
from aws_xray_sdk.core import xray_recorder
from aws_xray_sdk.core import patch_all
from configuration import Config
from sqs import handle_sqs_event
from utils import create_response, route_http_request
from log_seguro import resumir_evento, resumir_evento_sqs

patch_all()
config = Config()


@xray_recorder.capture('lambda_handler')
def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    try:
        # Log da requisição — sem cabeçalhos nem corpo (ver log_seguro).
        config.logger.info(
            f"Evento recebido: "
            f"{resumir_evento_sqs(event) if 'Records' in event else resumir_evento(event)}"
        )

        # Verificar tipo de evento
        if 'Records' in event:
            # Evento do SQS. A resposta segue o contrato de respostas parciais
            # de lote: só as mensagens listadas em `batchItemFailures` voltam
            # para a fila; as demais são confirmadas e removidas.
            # Lista vazia = lote inteiro processado com sucesso.
            falhas = handle_sqs_event(event)
            return {
                'batchItemFailures': [
                    {'itemIdentifier': identificador} for identificador in falhas
                ]
            }

        elif 'httpMethod' in event:
            # Requisição HTTP do API Gateway
            return route_http_request(event)

        else:
            # Evento direto (para testes)
            return create_response(400, {
                'error': 'Tipo de evento não suportado',
                'message': 'Esta Lambda suporta apenas eventos SQS e HTTP'
            })

    except Exception as e:
        config.logger.error(f"Erro inesperado no handler: {str(e)}", exc_info=True)
        if 'Records' in event:
            # Falha fora do laço por mensagem (ex.: evento malformado). Aqui não
            # há como atribuir a culpa a uma mensagem específica, então o lote
            # inteiro precisa voltar para a fila — e relançar é o que sinaliza
            # isso ao runtime.
            raise
        else:
            # Para HTTP, retornar erro
            return create_response(500, {
                'error': 'Erro interno do servidor',
                'message': 'Ocorreu um erro inesperado'
            })
