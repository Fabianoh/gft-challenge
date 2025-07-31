import json
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from aws_xray_sdk.core import xray_recorder
from aws_xray_sdk.core import patch_all
from configuration import Config

from operacoes import get_saldo_diario, get_saldo_anterior, calculate_saldo_diario, save_saldo_diario \
                     ,generate_relatorio_periodo, save_relatorio_to_s3

patch_all()
config = Config()


def create_response(status_code: int, body: Dict[str, Any], headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    default_headers = {
        'Content-Type': 'application/json',
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token',
        'Access-Control-Allow-Methods': 'GET,OPTIONS'
    }

    if headers:
        default_headers.update(headers)

    return {
        'statusCode': status_code,
        'headers': default_headers,
        'body': json.dumps(body, default=str, ensure_ascii=False)
    }

@xray_recorder.capture('handle_http_request')
def handle_http_request(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Manipula requisições HTTP GET para consulta de consolidado
    """
    try:
        # Verificar método HTTP
        http_method = event.get('httpMethod', '')
        if http_method == 'OPTIONS':
            return create_response(200, {'message': 'CORS preflight'})

        if http_method != 'GET':
            return create_response(405, {
                'error': 'Método não permitido',
                'message': 'Apenas GET é suportado'
            })

        # Extrair parâmetros de query
        query_params = event.get('queryStringParameters', {}) or {}

        # Parâmetros suportados
        data = query_params.get('data')  # Data específica (YYYY-MM-DD)
        data_inicio = query_params.get('data_inicio')  # Período início
        data_fim = query_params.get('data_fim')  # Período fim
        incluir_detalhes = query_params.get('incluir_detalhes', 'false').lower() == 'true'
        salvar_s3 = query_params.get('salvar_s3', 'false').lower() == 'true'

        # Validar parâmetros
        if data:
            # Consulta de saldo para data específica
            try:
                datetime.fromisoformat(data)
            except ValueError:
                return create_response(400, {
                    'error': 'Data inválida',
                    'message': 'Data deve estar no formato YYYY-MM-DD'
                })

            saldo = get_saldo_diario(data)
            if not saldo:
                # Calcular saldo se não existir
                saldo_anterior = get_saldo_anterior(data)
                saldo = calculate_saldo_diario(data, saldo_anterior)
                save_saldo_diario(saldo)

            response_data = {
                'success': True,
                'tipo': 'saldo_diario',
                'data': {
                    'data': saldo.data,
                    'saldo_inicial': float(saldo.saldo_inicial),
                    'total_creditos': float(saldo.total_creditos),
                    'total_debitos': float(saldo.total_debitos),
                    'saldo_final': float(saldo.saldo_final),
                    'quantidade_lancamentos': saldo.quantidade_lancamentos,
                    'ultima_atualizacao': saldo.ultima_atualizacao
                },
                'timestamp': datetime.now(timezone.utc).isoformat()
            }

        elif data_inicio and data_fim:
            # Consulta de relatório por período
            try:
                datetime.fromisoformat(data_inicio)
                datetime.fromisoformat(data_fim)
            except ValueError:
                return create_response(400, {
                    'error': 'Datas inválidas',
                    'message': 'Datas devem estar no formato YYYY-MM-DD'
                })

            if data_inicio > data_fim:
                return create_response(400, {
                    'error': 'Período inválido',
                    'message': 'Data início deve ser menor ou igual à data fim'
                })

            # Gerar relatório
            relatorio = generate_relatorio_periodo(data_inicio, data_fim)

            # Preparar resposta
            response_data = {
                'success': True,
                'tipo': 'relatorio_periodo',
                'data': {
                    'periodo_inicio': relatorio.periodo_inicio,
                    'periodo_fim': relatorio.periodo_fim,
                    'saldo_inicial_periodo': float(relatorio.saldo_inicial_periodo),
                    'saldo_final_periodo': float(relatorio.saldo_final_periodo),
                    'total_creditos_periodo': float(relatorio.total_creditos_periodo),
                    'total_debitos_periodo': float(relatorio.total_debitos_periodo),
                    'quantidade_dias': relatorio.quantidade_dias,
                    'resumo_movimentacao': {
                        'variacao_saldo': float(relatorio.saldo_final_periodo - relatorio.saldo_inicial_periodo),
                        'media_creditos_dia': float(
                            relatorio.total_creditos_periodo / relatorio.quantidade_dias) if relatorio.quantidade_dias > 0 else 0,
                        'media_debitos_dia': float(
                            relatorio.total_debitos_periodo / relatorio.quantidade_dias) if relatorio.quantidade_dias > 0 else 0
                    }
                },
                'timestamp': datetime.now(timezone.utc).isoformat()
            }

            # Incluir detalhes diários se solicitado
            if incluir_detalhes:
                response_data['data']['saldos_diarios'] = [
                    {
                        'data': saldo.data,
                        'saldo_inicial': float(saldo.saldo_inicial),
                        'total_creditos': float(saldo.total_creditos),
                        'total_debitos': float(saldo.total_debitos),
                        'saldo_final': float(saldo.saldo_final),
                        'quantidade_lancamentos': saldo.quantidade_lancamentos,
                        'ultima_atualizacao': saldo.ultima_atualizacao
                    }
                    for saldo in relatorio.saldos_diarios
                ]

            # Salvar no S3 se solicitado
            if salvar_s3:
                try:
                    s3_filename = save_relatorio_to_s3(relatorio)
                    response_data['s3_backup'] = {
                        'filename': s3_filename,
                        'bucket': config.S3_BUCKET
                    }
                except Exception as e:
                    config.logger.warning(f"Erro ao salvar no S3: {str(e)}")

        else:
            # Consulta de hoje se nenhum parâmetro fornecido
            hoje = datetime.now(timezone.utc).strftime('%Y-%m-%d')
            saldo = get_saldo_diario(hoje)
            if not saldo:
                saldo_anterior = get_saldo_anterior(hoje)
                saldo = calculate_saldo_diario(hoje, saldo_anterior)
                save_saldo_diario(saldo)

            response_data = {
                'success': True,
                'tipo': 'saldo_atual',
                'data': {
                    'data': saldo.data,
                    'saldo_inicial': float(saldo.saldo_inicial),
                    'total_creditos': float(saldo.total_creditos),
                    'total_debitos': float(saldo.total_debitos),
                    'saldo_final': float(saldo.saldo_final),
                    'quantidade_lancamentos': saldo.quantidade_lancamentos,
                    'ultima_atualizacao': saldo.ultima_atualizacao
                },
                'timestamp': datetime.now(timezone.utc).isoformat()
            }

        return create_response(200, response_data)

    except Exception as e:
        config.logger.error(f"Erro de consolidado: {str(e)}")
        return create_response(500, {
            'error': 'Erro interno',
            'message': str(e)
        })

    except BaseException as e:
        config.logger.error(f"Erro inesperado: {str(e)}", exc_info=True)
        return create_response(500, {
            'error': 'Erro interno do servidor',
            'message': 'Ocorreu um erro inesperado'
        })



