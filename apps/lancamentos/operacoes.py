import json
from typing import Dict, Any, Optional
from utils import create_response, validate_lancamento
from dynamodb import save_lancamento_to_dynamodb
from sqs import send_to_consolidacao_queue
from event_bridge import send_to_eventbridge
from datetime import datetime, timezone
from configuration import Config
from aws_xray_sdk.core import xray_recorder
from botocore.exceptions import ClientError

config = Config()

def cria_lancamento(event: Dict[str, Any]) -> Dict[str, Any]:
    try:
        # Extrair body da requisição
        body = event.get('body', '{}')
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError:
                return create_response(400, {
                    'error': 'JSON inválido',
                    'message': 'Body da requisição deve ser um JSON válido'
                })

        # Validar dados do lançamento
        try:
            validated_data = validate_lancamento(body)
        except Exception as e:
            return create_response(400, {
                'error': 'Dados inválidos',
                'message': str(e)
            })

        # Salvar no DynamoDB
        lancamento_id = save_lancamento_to_dynamodb(validated_data)

        # Enviar para fila de consolidação (assíncrono)
        send_to_consolidacao_queue(lancamento_id, validated_data)

        # Enviar evento para EventBridge (assíncrono)
        send_to_eventbridge(lancamento_id, validated_data)

        # Resposta de sucesso
        response_body = {
            'success': True,
            'message': 'Lançamento criado com sucesso',
            'data': {
                'id': lancamento_id,
                'tipo': validated_data['tipo'],
                'valor': float(validated_data['valor']),
                'descricao': validated_data['descricao'],
                'data': validated_data['data'],
                'categoria': validated_data['categoria'],
                'tags': validated_data['tags']
            },
            'timestamp': datetime.now(timezone.utc).isoformat()
        }

        config.logger.info(f"Lançamento processado com sucesso: {lancamento_id}")
        return create_response(201, response_body)

    except BaseException as e:
        config.logger.error(f"Erro inesperado: {e}", exc_info=True)
        return create_response(500, {
            'error': 'Erro interno do servidor',
            'message': 'Ocorreu um erro inesperado'
        })


def get_lancamentos_list(event: Dict[str, Any]) -> Dict[str, Any]:
    try:
        # Extrair parâmetros de query
        query_params = event.get('queryStringParameters', {}) or {}

        # Validar parâmetros de data
        data_inicio = query_params.get('data_inicio')
        data_fim = query_params.get('data_fim')

        if data_inicio:
            try:
                datetime.fromisoformat(data_inicio)
            except ValueError:
                return create_response(400, {
                    'error': 'Data início inválida',
                    'message': 'data_inicio deve estar no formato YYYY-MM-DD'
                })

        if data_fim:
            try:
                datetime.fromisoformat(data_fim)
            except ValueError:
                return create_response(400, {
                    'error': 'Data fim inválida',
                    'message': 'data_fim deve estar no formato YYYY-MM-DD'
                })

        if data_inicio and data_fim and data_inicio > data_fim:
            return create_response(400, {
                'error': 'Período inválido',
                'message': 'data_inicio deve ser menor ou igual a data_fim'
            })

        # Validar limit
        limit = query_params.get('limit', '50')
        try:
            limit_int = int(limit)
            if limit_int < 1 or limit_int > 100:
                raise ValueError()
        except ValueError:
            return create_response(400, {
                'error': 'Limit inválido',
                'message': 'limit deve ser um número entre 1 e 100'
            })

        # Validar offset
        offset = query_params.get('offset', '0')
        try:
            offset_int = int(offset)
            if offset_int < 0:
                raise ValueError()
        except ValueError:
            return create_response(400, {
                'error': 'Offset inválido',
                'message': 'offset deve ser um número maior ou igual a 0'
            })

        # Listar lançamentos
        result = list_lancamentos_with_filters(query_params)

        return create_response(200, {
            'success': True,
            'data': result,
            'timestamp': datetime.now(timezone.utc).isoformat()
        })


    except BaseException as e:
        config.logger.error(f"Erro ao listar lançamentos: {e}")
        return create_response(500, {
            'error': 'Erro interno',
            'message': 'Erro ao recuperar lista de lançamentos'
        })


def get_lancamento_individual(event: Dict[str, Any]) -> Dict[str, Any]:
    try:
        # Extrair ID do path
        path_parameters = event.get('pathParameters', {}) or {}
        lancamento_id = path_parameters.get('id')

        if not lancamento_id:
            return create_response(400, {
                'error': 'ID requerido',
                'message': 'ID do lançamento deve ser fornecido no path'
            })

        # Validar formato UUID (básico)
        if len(lancamento_id) != 36 or lancamento_id.count('-') != 4:
            return create_response(400, {
                'error': 'ID inválido',
                'message': 'ID deve estar no formato UUID válido'
            })

        # Buscar lançamento
        lancamento = get_lancamento_by_id(lancamento_id)

        if not lancamento:
            return create_response(404, {
                'error': 'Lançamento não encontrado',
                'message': f'Nenhum lançamento encontrado com ID: {lancamento_id}'
            })

        return create_response(200, {
            'success': True,
            'data': lancamento,
            'timestamp': datetime.now(timezone.utc).isoformat()
        })

    except BaseException as e:
        config.logger.error(f"Erro ao buscar lançamento individual: {e}")
        return create_response(500, {
            'error': 'Erro interno',
            'message': 'Erro ao recuperar lançamento'
        })


@xray_recorder.capture('list_lancamentos_with_filters')
def list_lancamentos_with_filters(filters: Dict[str, Any]) -> Dict[str, Any]:
    try:
        # Parâmetros de paginação
        limit = min(int(filters.get('limit', 50)), 100)  # Máximo 100
        offset = int(filters.get('offset', 0))

        # Parâmetros de filtro
        data_inicio = filters.get('data_inicio')
        data_fim = filters.get('data_fim')
        tipo = filters.get('tipo')
        categoria = filters.get('categoria')
        tags = filters.get('tags', '').split(',') if filters.get('tags') else []
        sort_order = filters.get('sort', 'data_desc')

        # Construir filtros para DynamoDB
        filter_expression_parts = []
        expression_values = {}
        expression_names = {}

        # Filtro por data
        if data_inicio or data_fim:
            if data_inicio and data_fim:
                filter_expression_parts.append('#data BETWEEN :data_inicio AND :data_fim')
                expression_values[':data_inicio'] = f"{data_inicio}T00:00:00"
                expression_values[':data_fim'] = f"{data_fim}T23:59:59"
            elif data_inicio:
                filter_expression_parts.append('#data >= :data_inicio')
                expression_values[':data_inicio'] = f"{data_inicio}T00:00:00"
            elif data_fim:
                filter_expression_parts.append('#data <= :data_fim')
                expression_values[':data_fim'] = f"{data_fim}T23:59:59"
            expression_names['#data'] = 'data'

        # Filtro por tipo
        if tipo and tipo in ['CREDITO', 'DEBITO']:
            filter_expression_parts.append('tipo = :tipo')
            expression_values[':tipo'] = tipo

        # Filtro por categoria
        if categoria:
            filter_expression_parts.append('categoria = :categoria')
            expression_values[':categoria'] = categoria

        # Filtro por status ativo
        filter_expression_parts.append('#status = :status')
        expression_values[':status'] = 'ATIVO'
        expression_names['#status'] = 'status'

        # Construir parâmetros da query
        scan_kwargs = {
            'FilterExpression': ' AND '.join(filter_expression_parts),
            'ExpressionAttributeValues': expression_values,
            'ExpressionAttributeNames': expression_names
        }

        # Executar scan com paginação
        all_items = []
        last_evaluated_key = None
        items_scanned = 0

        while True:
            if last_evaluated_key:
                scan_kwargs['ExclusiveStartKey'] = last_evaluated_key

            #TODO: Criar indice/GSI para evitar Scan
            response = config.tableLancamentos.scan(**scan_kwargs)
            items = response.get('Items', [])

            # Filtrar por tags se especificado
            if tags and tags[0]:  # Verificar se não é lista vazia
                items = [
                    item for item in items
                    if any(tag.strip() in item.get('tags', []) for tag in tags if tag.strip())
                ]

            all_items.extend(items)
            items_scanned += len(items)

            last_evaluated_key = response.get('LastEvaluatedKey')
            if not last_evaluated_key or len(all_items) >= (offset + limit):
                break

        # Ordenação
        if sort_order == 'data_desc':
            all_items.sort(key=lambda x: x.get('data', ''), reverse=True)
        elif sort_order == 'data_asc':
            all_items.sort(key=lambda x: x.get('data', ''))
        elif sort_order == 'valor_desc':
            all_items.sort(key=lambda x: float(x.get('valor', 0)), reverse=True)
        elif sort_order == 'valor_asc':
            all_items.sort(key=lambda x: float(x.get('valor', 0)))

        # Aplicar paginação
        total_items = len(all_items)
        paginated_items = all_items[offset:offset + limit]

        # Converter Decimal para float e limpar dados
        cleaned_items = []
        for item in paginated_items:
            cleaned_item = dict(item)
            if 'valor' in cleaned_item:
                cleaned_item['valor'] = float(cleaned_item['valor'])
            # Remover campos internos
            cleaned_item.pop('ambiente', None)
            cleaned_items.append(cleaned_item)

        # Calcular resumo financeiro
        total_creditos = sum(
            float(item['valor']) for item in all_items
            if item.get('tipo') == 'CREDITO'
        )
        total_debitos = sum(
            float(item['valor']) for item in all_items
            if item.get('tipo') == 'DEBITO'
        )

        return {
            'lancamentos': cleaned_items,
            'pagination': {
                'total': total_items,
                'limit': limit,
                'offset': offset,
                'has_more': offset + limit < total_items
            },
            'summary': {
                'total_creditos': total_creditos,
                'total_debitos': total_debitos,
                'saldo_liquido': total_creditos - total_debitos,
                'quantidade_total': total_items
            }
        }

    except ClientError as e:
        config.logger.error(f"Erro ao listar lançamentos: {e}")
        raise "Erro ao acessar dados dos lançamentos"


@xray_recorder.capture('get_lancamento_by_id')
def get_lancamento_by_id(lancamento_id: str) -> Optional[Dict[str, Any]]:
    try:
        response = config.tableLancamentos.get_item(Key={'id': lancamento_id})

        if 'Item' not in response:
            return None

        item = response['Item']

        # Converter Decimal para float
        if 'valor' in item:
            item['valor'] = float(item['valor'])

        config.logger.info(f"Lançamento recuperado: {lancamento_id}")
        return item

    except ClientError as e:
        config.logger.error(f"Erro ao recuperar lançamento: {str(e)}")
        raise "Erro ao acessar dados do lançamento"