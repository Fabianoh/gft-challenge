# Sistema de Controle de Fluxo de Caixa

### #######################################################################################
## 📋 Visão Geral
### #######################################################################################
Sistema serverless para controle de fluxo de caixa diário desenvolvido com arquitetura de microsserviços na AWS. O sistema permite registrar lançamentos (débitos e créditos) e gerar relatórios consolidados de saldo diário.

### #######################################################################################
### 🎯 Funcionalidades
### #######################################################################################
- **Registro de Lançamentos**: API para criar débitos e créditos
- **Consolidação Diária**: Cálculo automático de saldos diários
- **Relatórios**: Consulta de saldos por data ou período
- **Cache Inteligente**: Redis para otimização de consultas
- **Monitoramento**: Observabilidade completa com CloudWatch
- **Segurança**: WAF, criptografia e controle de acesso

### #######################################################################################
## 🏗️ Arquitetura
### #######################################################################################
### Domínios Funcionais

1. **Domínio de Lançamentos**
   - Registro de débitos e créditos
   - Validação de dados
   - Envio de eventos assíncronos

2. **Domínio de Consolidação**
   - Cálculo de saldos diários
   - Geração de relatórios
   - Cache de consultas

### #######################################################################################
### Fluxo de Dados
### #######################################################################################
'''
Cliente → Cloudfront -> S3 Website -> API Gateway → Lambda Lançamentos → DynamoDB
                                                       ↓
                                                    SQS Queue
                                                       ↓
                                                Lambda Consolidado → DynamoDB + Redis Cache
'''

### #######################################################################################
### Componentes AWS
### #######################################################################################
- **Cloudfront**: (não implementado)
    - ***Descrição***: CDN global da AWS que distribui conteúdo estático e dinâmico com baixa latência.
    - ***Motivo de uso***: performance global, cache inteligente, segurança integrada com WAF e contra DDoS, certificado SSL, roteamento avançado.
        
- **S3 Web Site Hosting**: (não implementado)
    - ***Descrição***: Hospedagem de site estático diretamente no Amazon S3
    - ***Motivo de uso***: simplicidade, alta disponibilidade, escalabilidade automática, custo mínimo,integração nativa com CloudFront, suport a versionamento de arquivos para rololback seguro.

- **API Gateway**: 
    - ***Descrição***: Ponto de entrada único para requisições e chamadas de regras de negócio (backend). Nesse projeto, as requisições são feitas diretamente na API
    - ***Motivo de uso***: Gerenciamento centralizado, rate limiting configurável, autenticação e autorização integrada com IAM, Cognito, API Key, Lambda Authorizer, validação de requests, CORS.

- **Lambda Authorizer**: (não implementado)
    - ***Descrição***: Função para realizar a autorizaão de uso da API pelo cliente.
    - ***Motivo de uso*** Automento da segurança.

- **Lambda Functions**: 
    - ***Descrição***: Processamento serverless para lógica de negócio sem gerenciamento de infraestrutura.
    - ***Motivo de uso***: Serverless, auto-escalável, pagamento por uso, integração que vários serviços AWS, isolamento de dominio, versionamento, observabilidade nativa.

- **DynamoDB**: 
    - ***Descrição***: Banco de dados NoSQL totalmente gerenciado para aplicações que exigem latência consistente em qualquer escala.
    - ***Motivo de uso***: Latência de single-digit millisecond em qualquer escala, escalabilidade ilimitada, alta disponibilidade, serverless, segurança com criptografia KMS.

- **SQS**: 
    - ***Descrição***: Comunicação assíncrona para desacoplar processamento de lançamentos e consolidação.
    - ***Motivo de uso***: Desacoplamento, resiliência, garantia de entrega, escalabilidade, integração com Lambda, baixo custo.

- **ElastiCache Redis**: 
    - ***Descrição***: Cache em memória para otimizar consultas frequentes de saldos consolidados.
    - ***Motivo de uso***: Altíssima performance, redução de custo, pois, diminui a carga no dynamoDB (RCU), multi-AZ, TTL automatico, monitoramento, segurança.

- **CloudWatch**: 
    - ***Descrição***: Monitoramento e logs centralizados para observabilidade completa do sistema.
    - ***Motivo de uso***: Unificação de observabilidade, métricas customizadas, alertas, dashboards, integração nativa com todos os serviços AWS.

- **X-Ray**: 
    - ***Descrição***: Tracing distribuído para análise de performance e debugging de requisições end-to-end.
    - ***Motivo de uso***: visibilidade, identificação de gargalos, mapa de serviços, analise de causa-raiz.

- **WAF**:
    - ***Descrição***: Firewall de aplicação web para proteção contra ataques comuns e filtragem de tráfego malicioso.
    - ***Motivo de uso***: proteção proativa, rate limiting avançado, proteção contra bot, regras customizadas, integração com CLoudfront.


### #######################################################################################
### Como executar esse projeto
### #######################################################################################
- **PASSO 1**:
Clone o repositório: https://github.com/Fabianoh/gft-challenge

- **PASSO 2**
Crie um bucket e uma role, cujo Principal seja cloudformation.amazonaws.com. Essa role deverá ter todas as permissões para que você crie os recursos AWS necessários nesse projeto, via cloudformation.
PS: se quiser, eu posso lhe passar o script dessa role.

- **PASSO 3**
Faça o upload dos scrips para dentro do bucket criado no passo 2. 
Se quiser, você pode usar o seguinte comando AWS CLI:
aws s3 sync gft-challenge/ s3://<NOME_BUCKET>
Substitua <NOME_BUCKET> pelo nome do bucket criado no passo 2.

- **PASSO **
Execute o comando AWS CLI abaixo:

aws cloudformation create-stack --stack-name gft-challenge --template-url https://s3.amazonaws.com/gft-challenge/infra/cfn/main.yml --parameters ParameterKey=AlertEmail,ParameterValue=fabianoh.alves@gmail.com ParameterKey=BucketCFN,ParameterValue=gft-challenge --capabilities CAPABILITY_NAMED_IAM

ou

Crie a stack diretamente na console AWS.

PS: Substitua os parâmetros <EMAIL_NOTIFICACAO> por um email à sua escolha e <NOME_BUCKET> pelo nome do bucket que foi criado no PASSO 2

- **PERMISSAS**
- Conta AWS criada e ativa
- AWS CLI instalado e configurado
- Role para criação e execução do cloudformation criada.