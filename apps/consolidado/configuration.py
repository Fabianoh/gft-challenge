import boto3
from os import environ
import logging

class Config():

    def __init__(self):
        self.s3Client = boto3.client('s3')
        self.s3Resource = boto3.resource('s3')
        self.dynamodbResource = boto3.resource('dynamodb')
        self.secrets_manager = boto3.client('secretsmanager')


        self.environment = environ.get('ENVIRONMENT')
        self.region = environ.get('REGION')
        self.account_id = environ.get('ACCOUNT_ID')
        self.DYNAMODB_TABLE_LANCAMENTOS = environ.get('DYNAMODB_TABLE_LANCAMENTOS')
        self.DYNAMODB_TABLE_CONSOLIDADO= environ.get('DYNAMODB_TABLE_CONSOLIDADO')
        self.SECRET_NAME = environ.get('SECRET_NAME')
        self.REDIS_ENDPOINT = environ.get('REDIS_ENDPOINT')
        self.REDIS_PORT = int(environ.get('REDIS_PORT', 6379))
        self.S3_BUCKET = environ['S3_BUCKET']

        self.tableLancamentos = self.dynamodbResource.Table(self.DYNAMODB_TABLE_LANCAMENTOS)
        self.tableConsolidado = self.dynamodbResource.Table(self.DYNAMODB_TABLE_CONSOLIDADO)

        self.function_name = f"Consolidado - {self.environment.upper()}"


        self.logger = logging.getLogger()
        self.logger.setLevel(logging.INFO)


