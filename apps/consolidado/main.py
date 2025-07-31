import json
from typing import Dict, Any
from aws_xray_sdk.core import xray_recorder
from aws_xray_sdk.core import patch_all
from configuration import Config
from sqs import handle_sqs_event
from utils import handle_http_request, create_response

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
            return handle_http_request(event)

        else:
            # Evento direto (para testes)
            return create_response(400, {
                'error': 'Tipo de evento não suportado',
                'message': 'Esta Lambda suporta apenas eventos SQS e HTTP'
            })

    except Exception as e:
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


# _secrets_cache = {}

# @dataclass
# class SaldoDiario:
#     """Classe para representar saldo diário"""
#     data: str
#     saldo_inicial: Decimal
#     total_creditos: Decimal
#     total_debitos: Decimal
#     saldo_final: Decimal
#     quantidade_lancamentos: int
#     ultima_atualizacao: str


# @dataclass
# class RelatorioConsolidado:
#     """Classe para relatório consolidado"""
#     periodo_inicio: str
#     periodo_fim: str
#     saldo_inicial_periodo: Decimal
#     saldo_final_periodo: Decimal
#     total_creditos_periodo: Decimal
#     total_debitos_periodo: Decimal
#     quantidade_dias: int
#     saldos_diarios: List[SaldoDiario]


# class ConsolidadoError(Exception):
#     """Exceção customizada para erros de consolidado"""
#     pass
#
#
# def get_secret(secret_name: str) -> Dict[str, Any]:
#     """
#     Recupera secrets do AWS Secrets Manager com cache
#     """
#     if secret_name in _secrets_cache:
#         return _secrets_cache[secret_name]
#
#     try:
#         response = config.secrets_manager.get_secret_value(SecretId=secret_name)
#         secret = json.loads(response['SecretString'])
#         _secrets_cache[secret_name] = secret
#         return secret
#     except ClientError as e:
#         config.logger.error(f"Erro ao recuperar secret: {str(e)}")
#         return {}

# # Função utilitária para testes manuais
# def test_consolidado():
#     """
#     Função para testes manuais da consolidação
#     """
#     hoje = datetime.now(timezone.utc).strftime('%Y-%m-%d')
#
#     # Testar cálculo de saldo diário
#     saldo = calculate_saldo_diario(hoje)
#     print(f"Saldo calculado para {hoje}: {saldo}")
#
#     # Testar relatório de período
#     data_inicio = (datetime.now(timezone.utc) - timedelta(days=7)).strftime('%Y-%m-%d')
#     relatorio = generate_relatorio_periodo(data_inicio, hoje)
#     print(f"Relatório período {data_inicio} a {hoje}: {relatorio}")
#
#
# if __name__ == "__main__":
#     # Para execução local/teste
#     test_consolidado()


