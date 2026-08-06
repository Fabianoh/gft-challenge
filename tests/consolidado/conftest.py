"""
Configuração da suíte do serviço de Consolidado.

Por que existe um conftest separado (e o Makefile roda pytest duas vezes):
lançamentos e consolidado são duas unidades de deploy independentes, cada uma
empacotada com seus módulos na raiz do .zip. As duas têm arquivos de mesmo nome
(`main.py`, `utils.py`, `operacoes.py`, `sqs.py`, `configuration.py`). Carregar
as duas no mesmo processo Python faria uma sobrescrever a outra em
`sys.modules` — uma colisão que não existe em produção, onde cada Lambda roda
isolada. Rodar em processos separados reproduz fielmente o isolamento real.
"""
import json
import os
import sys
from decimal import Decimal
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# 1. Ambiente falso
# ---------------------------------------------------------------------------
RAIZ = Path(__file__).resolve().parents[2]
APP = RAIZ / "apps" / "consolidado"
sys.path.insert(0, str(APP))

os.environ.update({
    "AWS_ACCESS_KEY_ID": "testing",
    "AWS_SECRET_ACCESS_KEY": "testing",
    "AWS_SECURITY_TOKEN": "testing",
    "AWS_SESSION_TOKEN": "testing",
    "AWS_DEFAULT_REGION": "us-east-1",
    "AWS_XRAY_SDK_ENABLED": "false",
    "ENVIRONMENT": "test",
    "REGION": "us-east-1",
    "ACCOUNT_ID": "123456789012",
    "DYNAMODB_TABLE_LANCAMENTOS": "gft-test-lancamentos",
    "DYNAMODB_TABLE_CONSOLIDADO": "gft-test-consolidado",
    "SECRET_NAME": "gft-test-secret",
    "S3_BUCKET": "gft-test-relatorios",
    # REDIS_ENDPOINT ausente de propósito: `get_redis_client` devolve None e o
    # código segue direto para o DynamoDB. Isso exercita justamente o caminho de
    # degradação graciosa quando o cache está fora do ar.
})
os.environ.pop("REDIS_ENDPOINT", None)

# ---------------------------------------------------------------------------
# 2. Mocks da AWS
# ---------------------------------------------------------------------------
from moto import mock_aws  # noqa: E402

_mock = mock_aws()
_mock.start()

import boto3  # noqa: E402

TABELA_LANCAMENTOS = os.environ["DYNAMODB_TABLE_LANCAMENTOS"]
TABELA_CONSOLIDADO = os.environ["DYNAMODB_TABLE_CONSOLIDADO"]

_dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
_dynamodb.create_table(
    TableName=TABELA_LANCAMENTOS,
    KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
    AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
    BillingMode="PAY_PER_REQUEST",
)
_dynamodb.create_table(
    TableName=TABELA_CONSOLIDADO,
    KeySchema=[{"AttributeName": "data", "KeyType": "HASH"}],
    AttributeDefinitions=[{"AttributeName": "data", "AttributeType": "S"}],
    BillingMode="PAY_PER_REQUEST",
)

boto3.client("s3", region_name="us-east-1").create_bucket(
    Bucket=os.environ["S3_BUCKET"]
)


# ---------------------------------------------------------------------------
# 3. Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def ambiente_limpo():
    for nome, chave in ((TABELA_LANCAMENTOS, "id"), (TABELA_CONSOLIDADO, "data")):
        tabela = _dynamodb.Table(nome)
        itens = tabela.scan().get("Items", [])
        with tabela.batch_writer() as lote:
            for item in itens:
                lote.delete_item(Key={chave: item[chave]})
    yield


@pytest.fixture
def tabela_lancamentos():
    return _dynamodb.Table(TABELA_LANCAMENTOS)


@pytest.fixture
def tabela_consolidado():
    return _dynamodb.Table(TABELA_CONSOLIDADO)


@pytest.fixture
def gravar_lancamento(tabela_lancamentos):
    """Insere um lançamento diretamente na tabela, montando o cenário do dia."""
    contador = {"n": 0}

    def _gravar(tipo, valor, data="2025-07-30T10:00:00", status="ATIVO"):
        contador["n"] += 1
        tabela_lancamentos.put_item(Item={
            "id": f"lanc-{contador['n']:04d}",
            "tipo": tipo,
            "valor": Decimal(str(valor)),
            "descricao": "lançamento de teste",
            "data": data,
            "categoria": "GERAL",
            "tags": [],
            "status": status,
            "ambiente": "test",
        })
    return _gravar


@pytest.fixture
def mensagem_sqs():
    """Monta um evento SQS no formato que o Lambda de consolidado recebe."""
    def _construir(data="2025-07-30T10:00:00", tipo="CREDITO", valor="100.00",
                   evento="LANCAMENTO_CRIADO", id_lancamento="lanc-0001"):
        return {
            "Records": [{
                "messageId": f"msg-{id_lancamento}",
                "body": json.dumps({
                    "eventType": evento,
                    "lancamentoId": id_lancamento,
                    "tipo": tipo,
                    "valor": float(valor),
                    "data": data,
                    "categoria": "GERAL",
                    "ambiente": "test",
                }),
            }]
        }
    return _construir


@pytest.fixture
def evento_http():
    """Monta um evento de API Gateway para os GETs do consolidado."""
    def _construir(resource="/consolidado", query=None, metodo="GET"):
        return {
            "httpMethod": metodo,
            "resource": resource,
            "queryStringParameters": query,
        }
    return _construir
