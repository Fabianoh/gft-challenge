"""
Executa o sistema completo localmente, sem conta AWS.

O que este script emula:
  * API Gateway  -> um roteador na porta 3000 que traduz HTTP em evento proxy
                    e encaminha para o serviço dono do recurso
  * Lambda        -> cada serviço roda em seu próprio processo, porque os dois
                     têm módulos de mesmo nome (`main.py`, `utils.py`, ...) e
                     colidiriam em `sys.modules` se compartilhassem o processo
  * EventSourceMapping -> uma thread no processo do consolidado faz long polling
                          na fila e invoca o handler com o mesmo formato de
                          evento `Records` que o Lambda receberia

Uso:
    docker compose up -d
    python scripts/preparar_ambiente_local.py
    python scripts/api_local.py

Depois:
    curl -X POST http://localhost:3000/lancamentos \
         -H 'Content-Type: application/json' \
         -d '{"tipo":"CREDITO","valor":"150.00","descricao":"Venda"}'

    curl http://localhost:3000/consolidado?data=$(date +%F)
"""
import argparse
import http.client
import json
import os
import signal
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, str(Path(__file__).parent))
import ambiente_local  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent

PORTA_ROTEADOR = 3000
PORTAS = {"lancamentos": 3001, "consolidado": 3002}

# Prefixo de caminho -> serviço responsável. É o equivalente local do mapa de
# recursos do API Gateway.
ROTAS = [
    ("/lancamentos", "lancamentos"),
    ("/consolidado", "consolidado"),
    ("/metricas", "consolidado"),
]


# ===========================================================================
# Tradução de HTTP para evento do API Gateway
# ===========================================================================
def decodificar_corpo(bytes_recebidos):
    """Transforma o corpo em texto sem nunca falhar.

    Nem todo cliente envia UTF-8: um `curl` em terminal Windows manda o corpo no
    code page local, e um "ã" chega como 0xE3. Um `decode('utf-8')` estrito
    levantaria UnicodeDecodeError dentro do handler, derrubando a conexão sem
    resposta — o cliente veria "connection reset", que não diz nada sobre a causa.

    O API Gateway repassa o corpo como bytes e deixa a aplicação decidir; aqui
    tentamos UTF-8 e caímos para latin-1, que aceita qualquer sequência de bytes.
    Assim um payload mal codificado vira erro de negócio (400), não queda de
    conexão.
    """
    try:
        return bytes_recebidos.decode("utf-8")
    except UnicodeDecodeError:
        return bytes_recebidos.decode("latin-1")


def montar_evento(metodo, caminho, query, corpo, headers):
    """Converte uma requisição HTTP no evento proxy que o Lambda espera.

    O campo `resource` precisa ser o template do recurso (com `{id}`), e não o
    caminho concreto: é assim que o API Gateway entrega e é sobre ele que o
    roteamento do handler decide.
    """
    partes = [p for p in caminho.strip("/").split("/") if p]
    path_parameters = None
    resource = "/" + "/".join(partes)

    if len(partes) == 2 and partes[0] == "lancamentos":
        resource = "/lancamentos/{id}"
        path_parameters = {"id": partes[1]}

    return {
        "httpMethod": metodo,
        "resource": resource,
        "path": caminho,
        "pathParameters": path_parameters,
        "queryStringParameters": {k: v[0] for k, v in query.items()} or None,
        "headers": headers,
        "body": corpo,
        "isBase64Encoded": False,
    }


# ===========================================================================
# Modo filho: um serviço
# ===========================================================================
def rodar_servico(servico, porta):
    ambiente_local.aplicar()
    sys.path.insert(0, str(RAIZ / "apps" / servico))

    from main import lambda_handler  # noqa: E402  (import tardio, pós-ambiente)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _responder(self, metodo):
            # Todo o processamento fica dentro do try: qualquer exceção que
            # escape daqui faria o socketserver fechar a conexão sem resposta, e
            # o cliente receberia "connection reset" em vez de um erro legível.
            try:
                url = urlparse(self.path)
                tamanho = int(self.headers.get("Content-Length") or 0)
                corpo = decodificar_corpo(self.rfile.read(tamanho)) if tamanho else None

                evento = montar_evento(
                    metodo, url.path, parse_qs(url.query), corpo, dict(self.headers)
                )

                resultado = lambda_handler(evento, None)
                status = resultado.get("statusCode", 200)
                payload = (resultado.get("body") or "").encode("utf-8")
                cabecalhos = resultado.get("headers", {})
            except Exception as erro:  # falha do handler vira 500, como no Lambda
                status = 500
                payload = json.dumps(
                    {"error": "Erro interno", "message": str(erro)}, ensure_ascii=False
                ).encode("utf-8")
                cabecalhos = {"Content-Type": "application/json"}

            self.send_response(status)
            for chave, valor in cabecalhos.items():
                self.send_header(chave, valor)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            self._responder("GET")

        def do_POST(self):
            self._responder("POST")

        def do_OPTIONS(self):
            self._responder("OPTIONS")

        def do_DELETE(self):
            self._responder("DELETE")

        def log_message(self, formato, *args):
            print(f"  [{servico}] {formato % args}", flush=True)

    if servico == "consolidado":
        threading.Thread(
            target=consumir_fila, args=(lambda_handler,), daemon=True
        ).start()

    print(f"  [{servico}] escutando em http://localhost:{porta}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", porta), Handler).serve_forever()


def consumir_fila(lambda_handler):
    """Equivalente local do AWS::Lambda::EventSourceMapping.

    Faz long polling na fila e entrega as mensagens ao handler no mesmo formato
    `{"Records": [...]}` que o Lambda receberia. Se o handler levantar exceção, a
    mensagem NÃO é apagada — ela volta para a fila e, após `maxReceiveCount`,
    o ElasticMQ a move para a DLQ, exatamente como o SQS faria.
    """
    import boto3

    sqs = boto3.client("sqs")
    url = os.environ["SQS_QUEUE_URL"]
    print("  [consolidado] consumidor da fila ativo", flush=True)

    while True:
        try:
            resposta = sqs.receive_message(
                QueueUrl=url, MaxNumberOfMessages=10, WaitTimeSeconds=5
            )
            mensagens = resposta.get("Messages", [])
            if not mensagens:
                continue

            evento = {"Records": [
                {"messageId": m["MessageId"], "body": m["Body"]} for m in mensagens
            ]}

            lambda_handler(evento, None)

            for mensagem in mensagens:
                sqs.delete_message(
                    QueueUrl=url, ReceiptHandle=mensagem["ReceiptHandle"]
                )
            print(f"  [consolidado] {len(mensagens)} evento(s) consolidado(s)", flush=True)

        except Exception as erro:
            print(f"  [consolidado] falha no consumo: {erro}", flush=True)


# ===========================================================================
# Modo pai: roteador + supervisão dos processos
# ===========================================================================
def servico_do_caminho(caminho):
    for prefixo, servico in ROTAS:
        if caminho == prefixo or caminho.startswith(prefixo + "/"):
            return servico
    return None


def rodar_roteador():
    class Roteador(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _encaminhar(self, metodo):
            url = urlparse(self.path)
            servico = servico_do_caminho(url.path)

            if servico is None:
                self._enviar(404, {"error": "Recurso não encontrado",
                                   "message": f"Nenhum serviço atende {url.path}"})
                return

            tamanho = int(self.headers.get("Content-Length") or 0)
            corpo = self.rfile.read(tamanho) if tamanho else None

            destino = f"http://127.0.0.1:{PORTAS[servico]}{self.path}"

            def montar():
                requisicao = urllib.request.Request(destino, data=corpo, method=metodo)
                for chave, valor in self.headers.items():
                    # Cabeçalhos hop-by-hop pertencem a UMA conexão e não podem
                    # ser repassados adiante (RFC 9110 §7.6.1). Repassar
                    # `Connection: keep-alive` fazia o servidor de destino e o
                    # cliente discordarem sobre o fim da resposta.
                    if chave.lower() not in ("host", "content-length", "connection",
                                             "keep-alive", "transfer-encoding",
                                             "te", "upgrade", "proxy-authorization"):
                        requisicao.add_header(chave, valor)
                requisicao.add_header("Connection", "close")
                return requisicao

            for tentativa in (1, 2):
                try:
                    with urllib.request.urlopen(montar(), timeout=60) as resposta:
                        self._repassar(resposta.status, resposta.read(),
                                       dict(resposta.headers))
                    return
                except urllib.error.HTTPError as erro:
                    self._repassar(erro.code, erro.read(), dict(erro.headers))
                    return
                except (urllib.error.URLError, ConnectionError,
                        http.client.RemoteDisconnected) as erro:
                    # A primeira requisição pode chegar antes de o processo filho
                    # aceitar conexões. Uma retentativa cobre essa janela.
                    if tentativa == 1:
                        continue
                    self._enviar(503, {"error": "Serviço indisponível",
                                       "message": f"{servico}: {erro}"})

        def _repassar(self, status, payload, cabecalhos):
            self.send_response(status)
            for chave, valor in cabecalhos.items():
                if chave.lower() not in ("transfer-encoding", "content-length",
                                         "connection"):
                    self.send_header(chave, valor)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _enviar(self, status, corpo):
            payload = json.dumps(corpo, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            self._encaminhar("GET")

        def do_POST(self):
            self._encaminhar("POST")

        def do_OPTIONS(self):
            self._encaminhar("OPTIONS")

        def do_DELETE(self):
            self._encaminhar("DELETE")

        def log_message(self, formato, *args):
            print(f"  [gateway] {formato % args}", flush=True)

    filhos = []
    for servico, porta in PORTAS.items():
        filhos.append(subprocess.Popen(
            [sys.executable, __file__, "--servico", servico, "--porta", str(porta)]
        ))

    def encerrar(*_):
        for filho in filhos:
            filho.terminate()
        sys.exit(0)

    signal.signal(signal.SIGINT, encerrar)
    signal.signal(signal.SIGTERM, encerrar)

    print(f"""
  Sistema de Fluxo de Caixa — ambiente local
  ------------------------------------------
  Entrada única:  http://localhost:{PORTA_ROTEADOR}

    POST /lancamentos              registra débito ou crédito
    GET  /lancamentos              lista com filtros
    GET  /lancamentos/<id>         consulta individual
    GET  /consolidado?data=...     saldo diário
    GET  /consolidado/relatorio    relatório por período
    GET  /metricas                 métricas agregadas

  Ctrl+C para encerrar.
""", flush=True)

    try:
        ThreadingHTTPServer(("127.0.0.1", PORTA_ROTEADOR), Roteador).serve_forever()
    except KeyboardInterrupt:
        encerrar()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--servico", choices=list(PORTAS))
    parser.add_argument("--porta", type=int)
    args = parser.parse_args()

    if args.servico:
        rodar_servico(args.servico, args.porta or PORTAS[args.servico])
    else:
        rodar_roteador()
