from configuration import Config
import uuid
from datetime import datetime, timezone
from typing import Dict, Any
from aws_xray_sdk.core import xray_recorder
from aws_xray_sdk.core import patch_all

patch_all()

config = Config()

@xray_recorder.capture('save_lancamento_to_dynamodb')
def save_lancamento_to_dynamodb(lancamento_data: Dict[str, Any]) -> str:
    # Gerar ID único
    lancamento_id = str(uuid.uuid4())

    # Preparar item para DynamoDB
    item = {
        'id': lancamento_id,
        'tipo': lancamento_data['tipo'],
        'valor': lancamento_data['valor'],
        'descricao': lancamento_data['descricao'],
        'data': lancamento_data['data'],
        'categoria': lancamento_data['categoria'],
        'tags': lancamento_data['tags'],
        'data_criacao': datetime.now(timezone.utc).isoformat(),
        'data_atualizacao': datetime.now(timezone.utc).isoformat(),
        'status': 'ATIVO',
        'ambiente': config.environment
    }

    try:
        # Salvar no DynamoDB
        config.tableLancamentos.put_item(Item=item)
        config.logger.info(f"Lançamento salvo com sucesso: {lancamento_id}")
        return lancamento_id

    except BaseException as e:
        config.logger.error(f"Erro ao salvar no DynamoDB: {str(e)}")
        raise "Erro ao salvar lançamento"