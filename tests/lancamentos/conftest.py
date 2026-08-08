"""
Configuração da suíte do serviço de Lançamentos.

Por que este arquivo é tão explícito na ordem das operações: os módulos da
aplicação executam `config = Config()` no nível do módulo, ou seja, a
configuração é lida no momento do `import`. Portanto o ambiente falso e os
mocks da AWS precisam existir ANTES de qualquer import do código da aplicação.
O pytest importa `conftest.py` antes dos arquivos de teste, então este é o
lugar correto para essa preparação.
"""
import os
import sys

from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# 1. Ambiente falso, definido antes de tudo
# ---------------------------------------------------------------------------
RAIZ = Path(__file__).resolve().parents[2]
APP = RAIZ / "apps" / "lancamentos"
sys.path.insert(0, str(APP))

os.environ.update({
    # Credenciais fictícias: garantem que nenhum teste alcance a AWS real
    "AWS_ACCESS_KEY_ID": "testing",
    "AWS_SECRET_ACCESS_KEY": "testing",
    "AWS_SECURITY_TOKEN": "testing",
    "AWS_SESSION_TOKEN": "testing",
    "AWS_DEFAULT_REGION": "us-east-1",
    # Desliga o X-Ray: fora do Lambda não existe segmento aberto e o SDK
    # lançaria erro de "cannot find the current segment" em cada decorator.
    "AWS_XRAY_SDK_ENABLED": "false",
    # Variáveis lidas por configuration.Config
    "ENVIRONMENT": "test",
    "REGION": "us-east-1",
    "ACCOUNT_ID": "123456789012",
    "DYNAMODB_TABLE_LANCAMENTOS": "gft-test-lancamentos",
    "SECRET_NAME": "gft-test-secret",
})

# ---------------------------------------------------------------------------
# 2. Mocks da AWS em memória (moto), ativos durante toda a sessão
# ---------------------------------------------------------------------------
from moto import mock_aws  # noqa: E402

_mock = mock_aws()
_mock.start()

import boto3  # noqa: E402

TABELA_LANCAMENTOS = os.environ["DYNAMODB_TABLE_LANCAMENTOS"]

_dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
_dynamodb.create_table(
    TableName=TABELA_LANCAMENTOS,
    KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
    AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
    BillingMode="PAY_PER_REQUEST",
)

_sqs = boto3.client("sqs", region_name="us-east-1")
_fila = _sqs.create_queue(QueueName="gft-test-consolidacao-queue")
# Só agora a URL da fila é conhecida — e ela precisa estar no ambiente antes
# do import de `sqs.py`, que a lê em tempo de import.
os.environ["SQS_QUEUE_URL"] = _fila["QueueUrl"]

# O barramento EventBridge "default" já existe no moto — nada a criar.


# ---------------------------------------------------------------------------
# 3. Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def ambiente_limpo():
    """Zera tabela e fila entre testes, para que a ordem de execução não importe."""
    tabela = _dynamodb.Table(TABELA_LANCAMENTOS)
    itens = tabela.scan().get("Items", [])
    with tabela.batch_writer() as lote:
        for item in itens:
            lote.delete_item(Key={"id": item["id"]})
    _sqs.purge_queue(QueueUrl=os.environ["SQS_QUEUE_URL"])
    yield


@pytest.fixture
def tabela():
    """Acesso direto à tabela, para montar cenário e conferir o resultado gravado."""
    return _dynamodb.Table(TABELA_LANCAMENTOS)


@pytest.fixture
def fila():
    """Cliente SQS e URL da fila de consolidação."""
    return _sqs, os.environ["SQS_QUEUE_URL"]


@pytest.fixture
def evento_post():
    """Constrói um evento de API Gateway para POST /lancamentos."""
    def _construir(body, como_texto=True):
        import json
        return {
            "httpMethod": "POST",
            "resource": "/lancamentos",
            "body": json.dumps(body) if como_texto else body,
        }
    return _construir


@pytest.fixture
def evento_get():
    """Constrói um evento de API Gateway para os GETs de lançamentos."""
    def _construir(resource="/lancamentos", path_params=None, query=None):
        return {
            "httpMethod": "GET",
            "resource": resource,
            "pathParameters": path_params,
            "queryStringParameters": query,
        }
    return _construir


@pytest.fixture
def lancamento_valido():
    """Payload mínimo que passa em todas as regras de validação.

    O valor vai como string, e não como número JSON, de propósito: é a forma
    recomendada para valores monetários em API. Um número JSON é lido como float
    pelo desserializador e já chega com a precisão comprometida.
    """
    return {
        "tipo": "CREDITO",
        "valor": "1500.50",
        "descricao": "Venda balcão",
        "categoria": "VENDAS",
    }
