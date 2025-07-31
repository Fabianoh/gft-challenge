import boto3
from os import environ
import logging

class Config():

    def __init__(self):
        self.s3Client = boto3.client('s3')
        self.s3Resource = boto3.resource('s3')
        self.dynamodbResource = boto3.resource('dynamodb')
        self.sqsClient = boto3.client('sqs')
        self.events = boto3.client('events')
        self.secrets_manager = boto3.client('secretsmanager')


        self.environment = environ.get('ENVIRONMENT')
        self.region = environ.get('REGION')
        self.account_id = environ.get('ACCOUNT_ID')
        self.DYNAMODB_TABLE_LANCAMENTOS = environ.get('DYNAMODB_TABLE_LANCAMENTOS')
        self.SQS_QUEUE_URL = environ.get('SQS_QUEUE_URL')
        self.ENVIRONMENT = environ.get('ENVIRONMENT')
        self.SECRET_NAME = environ.get('SECRET_NAME')

        self.tableLancamentos = self.dynamodbResource.Table(self.DYNAMODB_TABLE_LANCAMENTOS)

        self.function_name = f"Lancamentos - {self.environment.upper()}"


        self.logger = logging.getLogger()
        self.logger.setLevel(logging.INFO)


