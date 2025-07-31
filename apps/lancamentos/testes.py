from main import lambda_handler

context = ''
event = {
    'Payload': 'Insira o Payload a ser recebido pelo lambda'
}

lambda_handler(event, context)