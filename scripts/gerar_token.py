"""
Gera um token de acesso para chamar a API.

Existe porque, com autenticação ativa, a API implantada não responde sem token —
e sem uma forma simples de obter um, avaliar o projeto vira um exercício de
arqueologia. Em produção esse papel é do provedor de identidade (fluxo OAuth 2.0
client credentials); aqui o script assina localmente o mesmo formato de token
que o Lambda Authorizer valida.

Uso — buscando o segredo direto do Secrets Manager (precisa de credencial AWS):

    python scripts/gerar_token.py

Uso — informando o segredo manualmente:

    python scripts/gerar_token.py --segredo <valor> --emissor gft-challenge-prod

Escopos: por padrão o token recebe todos. Para demonstrar a autorização por
capacidade, restrinja:

    python scripts/gerar_token.py --escopos lancamentos:write
"""
import argparse
import base64
import hashlib
import hmac
import json
import sys
import time

ESCOPOS_DISPONIVEIS = [
    "lancamentos:write",
    "lancamentos:read",
    "consolidado:read",
    "metricas:read",
]


def _b64(dado: bytes) -> str:
    return base64.urlsafe_b64encode(dado).decode().rstrip("=")


def assinar(corpo: dict, segredo: str) -> str:
    cabecalho = _b64(json.dumps({"alg": "HS256", "typ": "JWT"},
                                separators=(",", ":")).encode())
    payload = _b64(json.dumps(corpo, separators=(",", ":")).encode())
    assinatura = hmac.new(
        segredo.encode(), f"{cabecalho}.{payload}".encode(), hashlib.sha256
    ).digest()
    return f"{cabecalho}.{payload}.{_b64(assinatura)}"


def buscar_segredo(projeto: str, ambiente: str) -> str:
    import boto3

    nome = f"{projeto}/{ambiente}/jwt"
    bruto = boto3.client("secretsmanager").get_secret_value(SecretId=nome)["SecretString"]
    try:
        return json.loads(bruto)["jwt_secret"]
    except (json.JSONDecodeError, KeyError):
        return bruto


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--projeto", default="gft-challenge-fluxo-caixa",
                        help="deve casar com o ProjectName da stack")
    parser.add_argument("--ambiente", default="dev",
                        help="deve casar com o Environment da stack")
    parser.add_argument("--segredo", help="valor do segredo; se omitido, busca no Secrets Manager")
    parser.add_argument("--emissor", help="padrão: <projeto>-<ambiente>")
    parser.add_argument("--audiencia", default="fluxo-de-caixa")
    parser.add_argument("--subject", default="pdv-loja-001")
    parser.add_argument("--comerciante", default="COM#0001")
    parser.add_argument("--escopos", nargs="*", default=ESCOPOS_DISPONIVEIS,
                        choices=ESCOPOS_DISPONIVEIS)
    parser.add_argument("--validade", type=int, default=3600, help="segundos")
    args = parser.parse_args()

    if args.segredo:
        segredo = args.segredo
    else:
        try:
            segredo = buscar_segredo(args.projeto, args.ambiente)
        except Exception as erro:
            print(f"Não foi possível ler o segredo no Secrets Manager: {erro}\n"
                  f"Informe manualmente com --segredo.", file=sys.stderr)
            return 1

    emissor = args.emissor or f"{args.projeto}-{args.ambiente}"
    agora = int(time.time())

    token = assinar({
        "sub": args.subject,
        "iss": emissor,
        "aud": args.audiencia,
        "iat": agora,
        "exp": agora + args.validade,
        "scope": " ".join(args.escopos),
        "comerciante_id": args.comerciante,
    }, segredo)

    print(f"""
Token gerado — válido por {args.validade}s
  emissor .... {emissor}
  audiência .. {args.audiencia}
  escopos .... {' '.join(args.escopos)}

{token}

Exemplo de uso:

  curl -H "Authorization: Bearer {token[:24]}..." \\
       https://<api>.execute-api.<regiao>.amazonaws.com/{args.ambiente}/consolidado
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
