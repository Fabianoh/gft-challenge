"""
Configuração de ambiente compartilhada pelos scripts de execução local.

Aponta o boto3 para os emuladores usando as variáveis `AWS_ENDPOINT_URL*`,
suportadas nativamente pelo botocore. Assim o código da aplicação roda local sem
nenhuma alteração — ele continua chamando `boto3.resource('dynamodb')` normalmente.

Regra inegociável deste arquivo: **nenhuma chamada pode escapar para a AWS real**.
Por isso `AWS_ENDPOINT_URL` (curinga, vale para todo serviço) é sempre definido.
Sem ele, serviços não emulados — EventBridge, S3, Secrets Manager — sairiam para
a internet, gastariam segundos em retentativas e falhariam com erro de credencial.
"""
import os

# Endpoint curinga: vale para qualquer serviço que não tenha override abaixo.
# O padrão é o moto server (`make up`), que emula todos os serviços numa porta só.
ENDPOINT_AWS = os.environ.get("ENDPOINT_AWS", "http://localhost:5000")

# Overrides por serviço, usados no modo docker-compose, em que DynamoDB e SQS
# rodam em contêineres dedicados.
ENDPOINT_DYNAMODB = os.environ.get("ENDPOINT_DYNAMODB")
ENDPOINT_SQS = os.environ.get("ENDPOINT_SQS")

TABELA_LANCAMENTOS = "gft-challenge-local-lancamentos"
TABELA_CONSOLIDADO = "gft-challenge-local-consolidado"
FILA_CONSOLIDACAO = "gft-challenge-local-consolidacao-queue"
FILA_DLQ = "gft-challenge-local-consolidacao-dlq"
BUCKET_RELATORIOS = "gft-challenge-local-relatorios"


def resolver_url_da_fila():
    """Descobre a URL real da fila perguntando ao serviço.

    Montar a URL na mão não funciona nos dois backends: ElasticMQ e moto usam
    identificadores de conta diferentes. Consultar é o único jeito portável.
    """
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError

    try:
        return boto3.client("sqs").get_queue_url(QueueName=FILA_CONSOLIDACAO)["QueueUrl"]
    except (ClientError, BotoCoreError):
        # A fila ainda não existe (o preparo não rodou). Devolve um palpite para
        # que o import da aplicação não quebre; a falha real aparece no uso.
        base = ENDPOINT_SQS or ENDPOINT_AWS
        return f"{base}/000000000000/{FILA_CONSOLIDACAO}"


def aplicar():
    """Prepara as variáveis de ambiente. Deve rodar ANTES de importar a app,
    porque `configuration.Config` lê o ambiente em tempo de import."""
    os.environ.update({
        # Credenciais fictícias: os emuladores não as validam, e credenciais
        # falsas garantem que nada seja aceito pela AWS real por acidente.
        "AWS_ACCESS_KEY_ID": "local",
        "AWS_SECRET_ACCESS_KEY": "local",
        "AWS_DEFAULT_REGION": "us-east-1",
        # Curinga: cobre EventBridge, S3, Secrets Manager e qualquer outro.
        "AWS_ENDPOINT_URL": ENDPOINT_AWS,
        # Sem daemon do X-Ray local; o SDK vira no-op.
        "AWS_XRAY_SDK_ENABLED": "false",
        # Variáveis lidas por configuration.Config
        "ENVIRONMENT": "local",
        "REGION": "us-east-1",
        "ACCOUNT_ID": "000000000000",
        "DYNAMODB_TABLE_LANCAMENTOS": TABELA_LANCAMENTOS,
        "DYNAMODB_TABLE_CONSOLIDADO": TABELA_CONSOLIDADO,
        "SECRET_NAME": "local",
        "S3_BUCKET": BUCKET_RELATORIOS,
    })

    # Overrides por serviço (modo docker-compose).
    if ENDPOINT_DYNAMODB:
        os.environ["AWS_ENDPOINT_URL_DYNAMODB"] = ENDPOINT_DYNAMODB
    if ENDPOINT_SQS:
        os.environ["AWS_ENDPOINT_URL_SQS"] = ENDPOINT_SQS

    # REDIS_ENDPOINT fica ausente de propósito: sem cache local, o código cai
    # direto no DynamoDB (degradação graciosa já implementada em redis_ops).
    os.environ.pop("REDIS_ENDPOINT", None)

    os.environ["SQS_QUEUE_URL"] = resolver_url_da_fila()
