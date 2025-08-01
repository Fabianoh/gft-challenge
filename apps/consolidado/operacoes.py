import json
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional
from aws_xray_sdk.core import xray_recorder
from aws_xray_sdk.core import patch_all
from decimal import Decimal
from configuration import Config
from dataclasses import dataclass, asdict
from redis_ops import get_from_cache, set_cache, invalidate_cache

patch_all()
config = Config()

@dataclass
class SaldoDiario:
    """Classe para representar saldo diário"""
    data: str
    saldo_inicial: Decimal
    total_creditos: Decimal
    total_debitos: Decimal
    saldo_final: Decimal
    quantidade_lancamentos: int
    ultima_atualizacao: str


@dataclass
class RelatorioConsolidado:
    """Classe para relatório consolidado"""
    periodo_inicio: str
    periodo_fim: str
    saldo_inicial_periodo: Decimal
    saldo_final_periodo: Decimal
    total_creditos_periodo: Decimal
    total_debitos_periodo: Decimal
    quantidade_dias: int
    saldos_diarios: List[SaldoDiario]

@xray_recorder.capture('get_lancamentos_by_date_range')
def get_lancamentos_by_date_range(data_inicio: str, data_fim: str) -> List[Dict[str, Any]]:
    try:
        #TODO: criar índice na tabela e retirar o scan
        response = config.tableLancamentos.scan(
            FilterExpression='#data BETWEEN :data_inicio AND :data_fim AND #status = :status',
            ExpressionAttributeNames={
                '#data': 'data',
                '#status': 'status'
            },
            ExpressionAttributeValues={
                ':data_inicio': data_inicio,
                ':data_fim': data_fim,
                ':status': 'ATIVO'
            }
        )

        return response['Items']

    except BaseException as e:
        config.logger.error(f"Erro ao recuperar lançamentos: {str(e)}")
        raise "Erro ao acessar dados de lançamentos"


@xray_recorder.capture('calculate_saldo_diario')
def calculate_saldo_diario(data: str, saldo_anterior: Decimal = Decimal('0')) -> SaldoDiario:
    """
    Calcula saldo diário para uma data específica
    """
    # Garantir que saldo_anterior seja Decimal
    if not isinstance(saldo_anterior, Decimal):
        saldo_anterior = Decimal(str(saldo_anterior))
    
    # Definir período (início e fim do dia)
    data_inicio = f"{data}T00:00:00"
    data_fim = f"{data}T23:59:59"

    # Recuperar lançamentos do dia
    lancamentos = get_lancamentos_by_date_range(data_inicio, data_fim)

    # Calcular totais
    total_creditos = Decimal('0')
    total_debitos = Decimal('0')

    for lancamento in lancamentos:
        # Garantir conversão correta para Decimal
        valor_raw = lancamento.get('valor', 0)
        if isinstance(valor_raw, Decimal):
            valor = valor_raw
        else:
            valor = Decimal(str(valor_raw))
        
        tipo = lancamento.get('tipo', '')
        if tipo == 'CREDITO':
            total_creditos += valor
        elif tipo == 'DEBITO':
            total_debitos += valor

    # Calcular saldo final
    saldo_final = saldo_anterior + total_creditos - total_debitos

    return SaldoDiario(
        data=data,
        saldo_inicial=saldo_anterior,
        total_creditos=total_creditos,
        total_debitos=total_debitos,
        saldo_final=saldo_final,
        quantidade_lancamentos=len(lancamentos),
        ultima_atualizacao=datetime.now(timezone.utc).isoformat()
    )


@xray_recorder.capture('save_saldo_diario')
def save_saldo_diario(saldo: SaldoDiario) -> None:
    try:
        item = {
            'data': saldo.data,
            'saldo_inicial': saldo.saldo_inicial,
            'total_creditos': saldo.total_creditos,
            'total_debitos': saldo.total_debitos,
            'saldo_final': saldo.saldo_final,
            'quantidade_lancamentos': saldo.quantidade_lancamentos,
            'ultima_atualizacao': saldo.ultima_atualizacao,
            'ambiente': config.environment
        }

        config.tableConsolidado.put_item(Item=item)
        config.logger.info(f"Saldo diário salvo: {saldo.data}")

    except BaseException as e:
        config.logger.error(f"Erro ao salvar saldo diário: {str(e)}")
        raise "Erro ao salvar consolidado"


@xray_recorder.capture('get_saldo_diario')
def get_saldo_diario(data: str, use_cache: bool = True) -> Optional[SaldoDiario]:
    """
    Recupera saldo diário do cache ou DynamoDB
    """
    cache_key = f"saldo_diario:{data}:{config.environment}"

    # Tentar recuperar do cache primeiro
    if use_cache:
        cached_data = get_from_cache(cache_key)
        if cached_data:
            try:
                # Garantir conversão correta dos tipos
                return SaldoDiario(
                    data=cached_data['data'],
                    saldo_inicial=Decimal(str(cached_data['saldo_inicial'])),
                    total_creditos=Decimal(str(cached_data['total_creditos'])),
                    total_debitos=Decimal(str(cached_data['total_debitos'])),
                    saldo_final=Decimal(str(cached_data['saldo_final'])),
                    quantidade_lancamentos=int(cached_data['quantidade_lancamentos']),
                    ultima_atualizacao=cached_data['ultima_atualizacao']
                )
            except (KeyError, ValueError, TypeError) as e:
                config.logger.warning(f"Erro ao reconstruir SaldoDiario do cache: {str(e)}")
                # Se houver erro, invalida o cache e continua para DynamoDB
                invalidate_cache(cache_key)

    try:
        response = config.tableConsolidado.get_item(Key={'data': data})

        if 'Item' in response:
            item = response['Item']
            saldo = SaldoDiario(
                data=item['data'],
                saldo_inicial=Decimal(str(item['saldo_inicial'])),
                total_creditos=Decimal(str(item['total_creditos'])),
                total_debitos=Decimal(str(item['total_debitos'])),
                saldo_final=Decimal(str(item['saldo_final'])),
                quantidade_lancamentos=int(item['quantidade_lancamentos']),
                ultima_atualizacao=item['ultima_atualizacao']
            )

            # Armazenar no cache
            if use_cache:
                # Converter Decimal para string para serialização JSON
                cache_data = {
                    'data': saldo.data,
                    'saldo_inicial': str(saldo.saldo_inicial),
                    'total_creditos': str(saldo.total_creditos),
                    'total_debitos': str(saldo.total_debitos),
                    'saldo_final': str(saldo.saldo_final),
                    'quantidade_lancamentos': saldo.quantidade_lancamentos,
                    'ultima_atualizacao': saldo.ultima_atualizacao
                }
                set_cache(cache_key, cache_data, ttl=3600)

            return saldo

    except BaseException as e:
        config.logger.error(f"Erro ao recuperar saldo diário: {str(e)}")

    return None


@xray_recorder.capture('get_saldo_anterior')
def get_saldo_anterior(data: str) -> Decimal:
    """
    Recupera saldo final do dia anterior
    """
    try:
        data_obj = datetime.fromisoformat(data)
        data_anterior = (data_obj - timedelta(days=1)).strftime('%Y-%m-%d')

        saldo_anterior = get_saldo_diario(data_anterior)
        if saldo_anterior:
            return saldo_anterior.saldo_final

    except BaseException as e:
        config.logger.warning(f"Erro ao recuperar saldo anterior: {str(e)}")

    return Decimal('0')


@xray_recorder.capture('generate_relatorio_periodo')
def generate_relatorio_periodo(data_inicio: str, data_fim: str) -> RelatorioConsolidado:
    cache_key = f"relatorio:{data_inicio}:{data_fim}:{config.environment}"

    # Tentar recuperar do cache
    cached_data = get_from_cache(cache_key)
    if cached_data:
        try:
            # Reconstruir objetos SaldoDiario do cache
            saldos_diarios = []
            for saldo_data in cached_data.get('saldos_diarios', []):
                saldo = SaldoDiario(
                    data=saldo_data['data'],
                    saldo_inicial=Decimal(str(saldo_data['saldo_inicial'])),
                    total_creditos=Decimal(str(saldo_data['total_creditos'])),
                    total_debitos=Decimal(str(saldo_data['total_debitos'])),
                    saldo_final=Decimal(str(saldo_data['saldo_final'])),
                    quantidade_lancamentos=int(saldo_data['quantidade_lancamentos']),
                    ultima_atualizacao=saldo_data['ultima_atualizacao']
                )
                saldos_diarios.append(saldo)
            
            return RelatorioConsolidado(
                periodo_inicio=cached_data['periodo_inicio'],
                periodo_fim=cached_data['periodo_fim'],
                saldo_inicial_periodo=Decimal(str(cached_data['saldo_inicial_periodo'])),
                saldo_final_periodo=Decimal(str(cached_data['saldo_final_periodo'])),
                total_creditos_periodo=Decimal(str(cached_data['total_creditos_periodo'])),
                total_debitos_periodo=Decimal(str(cached_data['total_debitos_periodo'])),
                quantidade_dias=int(cached_data['quantidade_dias']),
                saldos_diarios=saldos_diarios
            )
        except (KeyError, ValueError, TypeError) as e:
            config.logger.warning(f"Erro ao reconstruir RelatorioConsolidado do cache: {str(e)}")
            # Se houver erro, invalida o cache e recalcula
            invalidate_cache(cache_key)

    try:
        # Gerar lista de datas no período
        start_date = datetime.fromisoformat(data_inicio)
        end_date = datetime.fromisoformat(data_fim)

        saldos_diarios = []
        saldo_inicial_periodo = Decimal('0')
        total_creditos_periodo = Decimal('0')
        total_debitos_periodo = Decimal('0')

        current_date = start_date
        while current_date <= end_date:
            data_str = current_date.strftime('%Y-%m-%d')

            # Recuperar ou calcular saldo diário
            saldo = get_saldo_diario(data_str)
            if not saldo:
                saldo_anterior = get_saldo_anterior(data_str)
                saldo = calculate_saldo_diario(data_str, saldo_anterior)
                save_saldo_diario(saldo)

            saldos_diarios.append(saldo)

            # Acumular totais do período
            if current_date == start_date:
                saldo_inicial_periodo = saldo.saldo_inicial

            total_creditos_periodo += saldo.total_creditos
            total_debitos_periodo += saldo.total_debitos

            current_date += timedelta(days=1)

        # Saldo final do período
        saldo_final_periodo = saldos_diarios[-1].saldo_final if saldos_diarios else Decimal('0')

        relatorio = RelatorioConsolidado(
            periodo_inicio=data_inicio,
            periodo_fim=data_fim,
            saldo_inicial_periodo=saldo_inicial_periodo,
            saldo_final_periodo=saldo_final_periodo,
            total_creditos_periodo=total_creditos_periodo,
            total_debitos_periodo=total_debitos_periodo,
            quantidade_dias=len(saldos_diarios),
            saldos_diarios=saldos_diarios
        )

        # Armazenar no cache (converter Decimal para string)
        cache_data = {
            'periodo_inicio': relatorio.periodo_inicio,
            'periodo_fim': relatorio.periodo_fim,
            'saldo_inicial_periodo': str(relatorio.saldo_inicial_periodo),
            'saldo_final_periodo': str(relatorio.saldo_final_periodo),
            'total_creditos_periodo': str(relatorio.total_creditos_periodo),
            'total_debitos_periodo': str(relatorio.total_debitos_periodo),
            'quantidade_dias': relatorio.quantidade_dias,
            'saldos_diarios': [
                {
                    'data': saldo.data,
                    'saldo_inicial': str(saldo.saldo_inicial),
                    'total_creditos': str(saldo.total_creditos),
                    'total_debitos': str(saldo.total_debitos),
                    'saldo_final': str(saldo.saldo_final),
                    'quantidade_lancamentos': saldo.quantidade_lancamentos,
                    'ultima_atualizacao': saldo.ultima_atualizacao
                }
                for saldo in relatorio.saldos_diarios
            ]
        }
        set_cache(cache_key, cache_data, ttl=7200)

        return relatorio

    except BaseException as e:
        config.logger.error(f"Erro ao gerar relatório: {str(e)}")
        raise "Erro ao gerar relatório consolidado"


@xray_recorder.capture('save_relatorio_to_s3')
def save_relatorio_to_s3(relatorio: RelatorioConsolidado) -> str:
    """
    Salva relatório no S3 para backup/histórico
    """
    try:
        # Preparar dados do relatório (converter Decimal para string)
        relatorio_serializable = {
            'periodo_inicio': relatorio.periodo_inicio,
            'periodo_fim': relatorio.periodo_fim,
            'saldo_inicial_periodo': str(relatorio.saldo_inicial_periodo),
            'saldo_final_periodo': str(relatorio.saldo_final_periodo),
            'total_creditos_periodo': str(relatorio.total_creditos_periodo),
            'total_debitos_periodo': str(relatorio.total_debitos_periodo),
            'quantidade_dias': relatorio.quantidade_dias,
            'saldos_diarios': [
                {
                    'data': saldo.data,
                    'saldo_inicial': str(saldo.saldo_inicial),
                    'total_creditos': str(saldo.total_creditos),
                    'total_debitos': str(saldo.total_debitos),
                    'saldo_final': str(saldo.saldo_final),
                    'quantidade_lancamentos': saldo.quantidade_lancamentos,
                    'ultima_atualizacao': saldo.ultima_atualizacao
                }
                for saldo in relatorio.saldos_diarios
            ]
        }
        
        relatorio_data = {
            'relatorio': relatorio_serializable,
            'gerado_em': datetime.now(timezone.utc).isoformat(),
            'ambiente': config.environment,
            'versao': '1.0'
        }

        # Gerar nome do arquivo
        timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
        filename = f"relatorios/{config.environment}/{relatorio.periodo_inicio}_to_{relatorio.periodo_fim}_{timestamp}.json"

        # Salvar no S3
        config.s3Client.put_object(
            Bucket=config.S3_BUCKET,
            Key=filename,
            Body=json.dumps(relatorio_data, ensure_ascii=False, indent=2),
            ContentType='application/json',
            ServerSideEncryption='aws:kms'
        )

        config.logger.info(f"Relatório salvo no S3: {filename}")
        return filename

    except BaseException as e:
        config.logger.error(f"Erro ao salvar relatório no S3: {str(e)}")
        raise "Erro ao salvar relatório"


@xray_recorder.capture('recalculate_subsequent_balances')
def recalculate_subsequent_balances(data_inicio: str, max_days: int = 30) -> None:
    """
    Recalcula saldos dos dias seguintes após uma alteração
    """
    try:
        data_obj = datetime.fromisoformat(data_inicio)

        for i in range(1, max_days + 1):
            data_atual = (data_obj + timedelta(days=i)).strftime('%Y-%m-%d')

            # Verificar se há lançamentos para esta data
            if not has_lancamentos_for_date(data_atual):
                break

            # Recalcular saldo
            saldo_anterior = get_saldo_anterior(data_atual)
            saldo_diario = calculate_saldo_diario(data_atual, saldo_anterior)
            save_saldo_diario(saldo_diario)

            # Invalidar cache
            invalidate_cache(f"saldo_diario:{data_atual}:*")

    except BaseException as e:
        config.logger.warning(f"Erro ao recalcular saldos subsequentes: {str(e)}")


def has_lancamentos_for_date(data: str) -> bool:
    """
    Verifica se há lançamentos para uma data específica
    """
    data_inicio = f"{data}T00:00:00"
    data_fim = f"{data}T23:59:59"

    try:
        lancamentos = get_lancamentos_by_date_range(data_inicio, data_fim)
        return len(lancamentos) > 0
    except BaseException:
        return False


# # Função utilitária para debug de tipos
# def debug_types(obj, name="objeto"):
#     """
#     Função para debug de tipos de dados
#     """
#     config.logger.info(f"🔍 Debug {name}: tipo={type(obj)}, valor={obj}")
#     if hasattr(obj, '__dict__'):
#         for key, value in obj.__dict__.items():
#             config.logger.info(f"  {key}: tipo={type(value)}, valor={value}")