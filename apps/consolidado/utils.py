import json
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Dict, Any, Optional
from aws_xray_sdk.core import xray_recorder
from aws_xray_sdk.core import patch_all
from configuration import Config

from operacoes import get_saldo_diario, get_saldo_anterior, calculate_saldo_diario, save_saldo_diario \
                     ,generate_relatorio_periodo, save_relatorio_to_s3, get_lancamentos_by_date_range

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


def route_http_request(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Roteador para requisições HTTP baseado no resource path
    """
    try:
        http_method = event.get('httpMethod', '')
        resource_path = event.get('resource', '')
        
        config.logger.info(f"Roteando: {http_method} {resource_path}")
        
        # Handler para OPTIONS (CORS)
        if http_method == 'OPTIONS':
            return create_response(200, {'message': 'CORS preflight'})
        
        # Verificar se é GET
        if http_method != 'GET':
            return create_response(405, {
                'error': 'Método não permitido',
                'message': f'Método {http_method} não é suportado'
            })
        
        # Roteamento baseado no resource path
        if resource_path == '/consolidado':
            return handle_saldo_request(event)
        
        elif resource_path == '/consolidado/relatorio':
            return handle_relatorio_request(event)
        
        elif resource_path == '/metricas':
            return handle_metricas_request(event)
        
        else:
            return create_response(404, {
                'error': 'Recurso não encontrado',
                'message': f'Recurso {resource_path} não existe'
            })
        
    except BaseException as e:
        config.logger.error(f"Erro no roteamento: {str(e)}")
        return create_response(500, {
            'error': 'Erro interno',
            'message': 'Erro no roteamento da requisição'
        })
    


@xray_recorder.capture('handle_saldo_request')
def handle_saldo_request(event: Dict[str, Any]) -> Dict[str, Any]:
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
    


@xray_recorder.capture('handle_relatorio_request')
def handle_relatorio_request(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handler para GET /consolidado/relatorio
    Gera relatório consolidado para um período
    """
    try:
        query_params = event.get('queryStringParameters', {}) or {}
        
        # Parâmetros obrigatórios
        data_inicio = query_params.get('data_inicio')
        data_fim = query_params.get('data_fim')
        
        if not data_inicio or not data_fim:
            return create_response(400, {
                'error': 'Parâmetros obrigatórios',
                'message': 'data_inicio e data_fim são obrigatórios'
            })
        
        # Validar datas
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
                'message': 'data_inicio deve ser menor ou igual a data_fim'
            })
        
        # Parâmetros opcionais
        formato = query_params.get('formato', 'json')
        incluir_detalhes = query_params.get('incluir_detalhes', 'false').lower() == 'true'
        salvar_s3 = query_params.get('salvar_s3', 'false').lower() == 'true'
        
        # Gerar relatório
        relatorio = generate_relatorio_periodo(data_inicio, data_fim)
        
        # Conversões seguras para float
        saldo_inicial = safe_decimal_to_float(relatorio.saldo_inicial_periodo)
        saldo_final = safe_decimal_to_float(relatorio.saldo_final_periodo)
        total_creditos = safe_decimal_to_float(relatorio.total_creditos_periodo)
        total_debitos = safe_decimal_to_float(relatorio.total_debitos_periodo)
        quantidade_dias = safe_int_conversion(relatorio.quantidade_dias)
        
        # Calcular percentual de crescimento
        percentual_crescimento = 0.0
        if saldo_inicial > 0:
            percentual_crescimento = ((saldo_final - saldo_inicial) / saldo_inicial) * 100
        
        # Preparar resposta
        response_data = {
            'success': True,
            'tipo': 'relatorio_periodo',
            'data': {
                'periodo': {
                    'inicio': relatorio.periodo_inicio,
                    'fim': relatorio.periodo_fim,
                    'quantidade_dias': quantidade_dias
                },
                'resumo_financeiro': {
                    'saldo_inicial_periodo': saldo_inicial,
                    'saldo_final_periodo': saldo_final,
                    'total_creditos_periodo': total_creditos,
                    'total_debitos_periodo': total_debitos,
                    'variacao_liquida': saldo_final - saldo_inicial,
                    'percentual_crescimento': round(percentual_crescimento, 2)
                }
            },
            'metadados': {
                'gerado_em': datetime.now(timezone.utc).isoformat(),
                'formato': formato,
                'incluir_detalhes': incluir_detalhes
            },
            'timestamp': datetime.now(timezone.utc).isoformat()
        }
        
        # Incluir detalhes se solicitado
        if incluir_detalhes:
            response_data['data']['saldos_diarios'] = [
                {
                    'data': saldo.data,
                    'saldo_inicial': safe_decimal_to_float(saldo.saldo_inicial),
                    'total_creditos': safe_decimal_to_float(saldo.total_creditos),
                    'total_debitos': safe_decimal_to_float(saldo.total_debitos),
                    'saldo_final': safe_decimal_to_float(saldo.saldo_final),
                    'quantidade_lancamentos': safe_int_conversion(saldo.quantidade_lancamentos)
                }
                for saldo in relatorio.saldos_diarios
            ]
        
        # Salvar no S3 se solicitado
        if salvar_s3:
            try:
                s3_filename = save_relatorio_to_s3(relatorio)
                response_data['arquivos'] = {
                    's3_backup': {
                        'bucket': config.S3_BUCKET,
                        'key': s3_filename
                    }
                }
            except Exception as e:
                config.logger.warning(f"Erro ao salvar no S3: {str(e)}")
        
        return create_response(200, response_data)
        
    except Exception as e:
        config.logger.error(f"Erro ao gerar relatório: {str(e)}", exc_info=True)
        return create_response(500, {
            'error': 'Erro interno',
            'message': f'Erro ao gerar relatório: {str(e)}'
        })


@xray_recorder.capture('handle_metricas_request')
def handle_metricas_request(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handler para GET /metricas
    Retorna métricas resumidas do sistema
    """
    try:
        query_params = event.get('queryStringParameters', {}) or {}
        
        # Parâmetros
        periodo = query_params.get('periodo', '30d')
        incluir_tendencias = query_params.get('incluir_tendencias', 'false').lower() == 'true'
        
        # Validar período
        if periodo not in ['7d', '30d', '90d', '1y']:
            return create_response(400, {
                'error': 'Período inválido',
                'message': 'Período deve ser: 7d, 30d, 90d ou 1y'
            })
        
        # Calcular datas baseado no período
        hoje = datetime.now(timezone.utc)
        if periodo == '7d':
            data_inicio = (hoje - timedelta(days=7)).strftime('%Y-%m-%d')
        elif periodo == '30d':
            data_inicio = (hoje - timedelta(days=30)).strftime('%Y-%m-%d')
        elif periodo == '90d':
            data_inicio = (hoje - timedelta(days=90)).strftime('%Y-%m-%d')
        else:  # 1y
            data_inicio = (hoje - timedelta(days=365)).strftime('%Y-%m-%d')
        
        data_fim = hoje.strftime('%Y-%m-%d')
        
        # Buscar lançamentos do período
        data_inicio_iso = f"{data_inicio}T00:00:00"
        data_fim_iso = f"{data_fim}T23:59:59"
        
        lancamentos = get_lancamentos_by_date_range(data_inicio_iso, data_fim_iso)
        
        # Calcular métricas com conversões seguras
        total_lancamentos = len(lancamentos)
        total_creditos = 0.0
        total_debitos = 0.0
        
        for lancamento in lancamentos:
            valor = safe_decimal_to_float(lancamento.get('valor', 0))
            tipo = lancamento.get('tipo', '')
            
            if tipo == 'CREDITO':
                total_creditos += valor
            elif tipo == 'DEBITO':
                total_debitos += valor
        
        saldo_liquido = total_creditos - total_debitos
        
        # Calcular dias com dados
        dias_periodo = (datetime.fromisoformat(data_fim) - datetime.fromisoformat(data_inicio)).days + 1
        
        # Agrupar por data para análises
        lancamentos_por_data = {}
        for lancamento in lancamentos:
            data_lanc = lancamento['data'][:10]  # YYYY-MM-DD
            if data_lanc not in lancamentos_por_data:
                lancamentos_por_data[data_lanc] = []
            lancamentos_por_data[data_lanc].append(lancamento)
        
        dias_com_movimentacao = len(lancamentos_por_data)
        
        # Calcular maior e menor volume diário
        volumes_diarios = []
        for data, lancs in lancamentos_por_data.items():
            volume_dia = sum(safe_decimal_to_float(l.get('valor', 0)) for l in lancs)
            volumes_diarios.append(volume_dia)
        
        maior_volume_dia = max(volumes_diarios) if volumes_diarios else 0.0
        menor_volume_dia = min(volumes_diarios) if volumes_diarios else 0.0
        
        response_data = {
            'success': True,
            'data': {
                'periodo_analise': periodo,
                'periodo_datas': {
                    'inicio': data_inicio,
                    'fim': data_fim,
                    'dias_total': dias_periodo
                },
                'resumo': {
                    'total_lancamentos': total_lancamentos,
                    'total_creditos': round(total_creditos, 2),
                    'total_debitos': round(total_debitos, 2),
                    'saldo_liquido': round(saldo_liquido, 2)
                },
                'performance': {
                    'dias_com_movimentacao': dias_com_movimentacao,
                    'dias_sem_movimentacao': dias_periodo - dias_com_movimentacao,
                    'media_lancamentos_dia': round(total_lancamentos / dias_periodo, 2) if dias_periodo > 0 else 0.0,
                    'maior_volume_dia': round(maior_volume_dia, 2),
                    'menor_volume_dia': round(menor_volume_dia, 2)
                }
            },
            'timestamp': datetime.now(timezone.utc).isoformat()
        }
        
        # Incluir análise de tendências se solicitado
        if incluir_tendencias and len(volumes_diarios) > 1:
            try:
                # Análise simples de tendência
                primeira_metade = volumes_diarios[:len(volumes_diarios)//2]
                segunda_metade = volumes_diarios[len(volumes_diarios)//2:]
                
                media_primeira = sum(primeira_metade) / len(primeira_metade) if primeira_metade else 0.0
                media_segunda = sum(segunda_metade) / len(segunda_metade) if segunda_metade else 0.0
                
                if media_segunda > media_primeira * 1.1:
                    direcao = "CRESCIMENTO"
                    intensidade = "ALTA" if media_segunda > media_primeira * 1.5 else "MODERADA"
                elif media_segunda < media_primeira * 0.9:
                    direcao = "DECLÍNIO"
                    intensidade = "ALTA" if media_segunda < media_primeira * 0.5 else "MODERADA"
                else:
                    direcao = "ESTÁVEL"
                    intensidade = "BAIXA"
                
                crescimento_medio = 0.0
                if len(primeira_metade) > 0:
                    crescimento_medio = (media_segunda - media_primeira) / len(primeira_metade)
                
                response_data['data']['tendencias'] = {
                    'direcao': direcao,
                    'intensidade': intensidade,
                    'crescimento_medio_diario': round(crescimento_medio, 2)
                }
                
            except Exception as e:
                config.logger.warning(f"Erro ao calcular tendências: {str(e)}")
                # Continuar sem tendências se houver erro
        
        return create_response(200, response_data)
        
    except Exception as e:
        config.logger.error(f"Erro ao obter métricas: {str(e)}", exc_info=True)
        return create_response(500, {
            'error': 'Erro interno',
            'message': f'Erro ao calcular métricas: {str(e)}'
        })


def create_response(status_code: int, body: Dict[str, Any], headers: Dict[str, str] = None) -> Dict[str, Any]:
    """
    Cria resposta HTTP padronizada (função helper)
    """
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


def safe_decimal_to_float(value: Any) -> float:
    """Converte Decimal para float de forma segura"""
    if isinstance(value, Decimal):
        return float(value)
    elif isinstance(value, (int, float)):
        return float(value)
    elif isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return 0.0
    else:
        return 0.0

def safe_int_conversion(value: Any) -> int:
    """Converte valor para int de forma segura"""
    if isinstance(value, int):
        return value
    elif isinstance(value, (float, Decimal)):
        return int(value)
    elif isinstance(value, str):
        try:
            return int(float(value))
        except ValueError:
            return 0
    else:
        return 0