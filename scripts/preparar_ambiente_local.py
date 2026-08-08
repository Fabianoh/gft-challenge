"""
Cria as tabelas do DynamoDB local. Idempotente: pode rodar quantas vezes quiser.

Uso:  python scripts/preparar_ambiente_local.py
"""
import sys

import ambiente_local

ambiente_local.aplicar()

import boto3  # noqa: E402
from botocore.exceptions import ClientError  # noqa: E402


TABELAS = [
    {
        "TableName": ambiente_local.TABELA_LANCAMENTOS,
        "KeySchema": [{"AttributeName": "id", "KeyType": "HASH"}],
        "AttributeDefinitions": [
            {"AttributeName": "id", "AttributeType": "S"},
            {"AttributeName": "data", "AttributeType": "S"},
            {"AttributeName": "tipo", "AttributeType": "S"},
        ],
        # Espelha o GSI de infra/cfn/dynamodb.yml para que o ambiente local
        # reproduza a mesma modelagem (inclusive suas limitações).
        "GlobalSecondaryIndexes": [{
            "IndexName": "data-tipo-index",
            "KeySchema": [
                {"AttributeName": "data", "KeyType": "HASH"},
                {"AttributeName": "tipo", "KeyType": "RANGE"},
            ],
            "Projection": {"ProjectionType": "ALL"},
        }],
        "BillingMode": "PAY_PER_REQUEST",
    },
    {
        "TableName": ambiente_local.TABELA_CONSOLIDADO,
        "KeySchema": [{"AttributeName": "data", "KeyType": "HASH"}],
        "AttributeDefinitions": [{"AttributeName": "data", "AttributeType": "S"}],
        "BillingMode": "PAY_PER_REQUEST",
    },
]


def main():
    dynamodb = boto3.client("dynamodb")

    for definicao in TABELAS:
        nome = definicao["TableName"]
        try:
            dynamodb.create_table(**definicao)
            print(f"  [criada]  {nome}")
        except ClientError as erro:
            if erro.response["Error"]["Code"] == "ResourceInUseException":
                print(f"  [existe]  {nome}")
            else:
                raise

    # No modo Docker, o ElasticMQ já cria a fila a partir de scripts/elasticmq.conf.
    # No modo sem Docker (moto server), ela precisa ser criada aqui.
    sqs = boto3.client("sqs")
    filas = sqs.list_queues().get("QueueUrls", [])
    if any(ambiente_local.FILA_CONSOLIDACAO in url for url in filas):
        print(f"  [existe]  {ambiente_local.FILA_CONSOLIDACAO}")
    else:
        sqs.create_queue(QueueName=ambiente_local.FILA_DLQ)
        sqs.create_queue(QueueName=ambiente_local.FILA_CONSOLIDACAO)
        print(f"  [criada]  {ambiente_local.FILA_CONSOLIDACAO}")

    print("\nAmbiente local pronto. Suba a API com: python scripts/api_local.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
