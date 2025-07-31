import json
from configuration import Config
from typing import Dict, Any
from aws_xray_sdk.core import xray_recorder
from aws_xray_sdk.core import patch_all

patch_all()

config = Config()

@xray_recorder.capture('send_to_eventbridge')
def send_to_eventbridge(lancamento_id: str, lancamento_data: Dict[str, Any]) -> None:
    """
    Envia evento para o EventBridge
    """
    try:
        # Preparar evento
        event_detail = {
            'lancamentoId': lancamento_id,
            'tipo': lancamento_data['tipo'],
            'valor': float(lancamento_data['valor']),
            'data': lancamento_data['data'],
            'categoria': lancamento_data['categoria'],
            'ambiente': config.environment
        }

        # Enviar para EventBridge
        response = config.events.put_events(
            Entries=[
                {
                    'Source': f'controle-fluxo-caixa.lancamentos',
                    'DetailType': 'Lançamento Criado',
                    'Detail': json.dumps(event_detail, default=str),
                    'Resources': [
                        f'arn:aws:dynamodb:{config.region}:{config.account_id}:table/{config.DYNAMODB_TABLE_LANCAMENTOS}']
                }
            ]
        )

        config.logger.info(f"Evento enviado para EventBridge: {response['Entries'][0]['EventId']}")

    except BaseException as e:
        config.logger.error(f"Erro ao enviar para EventBridge: {str(e)}")
        # Não falhar a requisição por erro no EventBridge