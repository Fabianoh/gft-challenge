import json
import configuration
from utils import create_response
from typing import Dict, Any
from aws_xray_sdk.core import xray_recorder
from operacoes import cria_lancamento, get_lancamentos_list, get_lancamento_individual

config = configuration.Config()

@xray_recorder.capture('lambda_handler')
def lambda_handler(event, context) -> Dict[str, Any]:
    try:
        # Log da requisição
        config.logger.info(f"Evento recebido: {json.dumps(event, default=str)}")

        # Verificar método HTTP
        http_method = event.get('httpMethod', '')
        resource_path = event.get('resource', '')

        # Handler para OPTIONS (CORS)
        if http_method == 'OPTIONS':
            return create_response(200, {'message': 'CORS preflight'})

        # Roteamento baseado no método e resource
        if http_method == 'POST' and resource_path == '/lancamentos':
            return cria_lancamento(event)

        elif http_method == 'GET' and resource_path == '/lancamentos':
            return get_lancamentos_list(event)

        elif http_method == 'GET' and resource_path == '/lancamentos/{id}':
            return get_lancamento_individual(event)

        else:
            return create_response(405, {
                'error': 'Método não permitido',
                'message': f'Método {http_method} não é suportado para o recurso {resource_path}'
            })

    except Exception as e:
        config.logger.error(f"Erro inesperado no handler principal: {e}", exc_info=True)
        return create_response(500, {
            'error': 'Erro interno do servidor',
            'message': 'Ocorreu um erro inesperado'
        })