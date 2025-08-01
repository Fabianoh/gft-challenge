# Sistema de Controle de Fluxo de Caixa

## 📋 Visão Geral

Sistema serverless para controle de fluxo de caixa diário desenvolvido com arquitetura de microsserviços na AWS. O sistema permite registrar lançamentos (débitos e créditos) e gerar relatórios consolidados de saldo diário.

### 🎯 Funcionalidades

- **Registro de Lançamentos**: API para criar débitos e créditos
- **Consolidação Diária**: Cálculo automático de saldos diários
- **Relatórios**: Consulta de saldos por data ou período
- **Cache Inteligente**: Redis para otimização de consultas
- **Monitoramento**: Observabilidade completa com CloudWatch
- **Segurança**: WAF, criptografia e controle de acesso

## 🏗️ Arquitetura

### Domínios Funcionais

1. **Domínio de Lançamentos**
   - Registro de débitos e créditos
   - Validação de dados
   - Envio de eventos assíncronos

2. **Domínio de Consolidação**
   - Cálculo de saldos diários
   - Geração de relatórios
   - Cache de consultas

### Fluxo de Dados

'''
Cliente → API Gateway → Lambda Lançamentos → DynamoDB
                           ↓
                        SQS Queue
                           ↓
                    Lambda Consolidado → DynamoDB + Redis Cache
'''

### Componentes AWS

- **API Gateway**: Ponto de entrada único
- **Lambda Functions**: Processamento serverless
- **DynamoDB**: Banco de dados NoSQL
- **SQS**: Comunicação assíncrona
- **ElastiCache Redis**: Cache de consultas
- **S3**: Backup e relatórios
- **CloudWatch**: Monitoramento e logs
- **X-Ray**: Tracing distribuído
- **WAF**: