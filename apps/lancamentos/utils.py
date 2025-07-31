import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, Any, Optional
from aws_xray_sdk.core import patch_all

patch_all()



def create_response(status_code: int, body: Dict[str, Any], headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """
    Cria resposta HTTP padronizada
    """
    default_headers = {
        'Content-Type': 'application/json',
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token',
        'Access-Control-Allow-Methods': 'POST,OPTIONS'
    }

    if headers:
        default_headers.update(headers)

    return {
        'statusCode': status_code,
        'headers': default_headers,
        'body': json.dumps(body, default=str, ensure_ascii=False)
    }


def validate_lancamento(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Valida os dados do lançamento
    """
    required_fields = ['tipo', 'valor', 'descricao']

    # Verificar campos obrigatórios
    for field in required_fields:
        if field not in data or not data[field]:
            raise ValidationError(f"Campo '{field}' é obrigatório")

    # Validar tipo
    if data['tipo'] not in ['DEBITO', 'CREDITO']:
        raise ValidationError("Tipo deve ser 'DEBITO' ou 'CREDITO'")

    # Validar valor
    try:
        valor = Decimal(str(data['valor']))
        if valor <= 0:
            raise ValidationError("Valor deve ser maior que zero")
    except (ValueError, TypeError):
        raise ValidationError("Valor deve ser um número válido")

    # Validar descrição
    descricao = str(data['descricao']).strip()
    if len(descricao) < 1 or len(descricao) > 255:
        raise ValidationError("Descrição deve ter entre 1 e 255 caracteres")

    # Validar/definir data
    data_lancamento = data.get('data')
    if data_lancamento:
        try:
            datetime.fromisoformat(data_lancamento.replace('Z', '+00:00'))
        except ValueError:
            raise ValidationError("Data deve estar no formato ISO 8601")
    else:
        data_lancamento = datetime.now(timezone.utc).isoformat()

    return {
        'tipo': data['tipo'],
        'valor': valor,
        'descricao': descricao,
        'data': data_lancamento,
        'categoria': data.get('categoria', 'GERAL'),
        'tags': data.get('tags', [])
    }