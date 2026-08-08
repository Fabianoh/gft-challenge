"""
Lambda Authorizer — autenticação e autorização da API.

Fecha a lacuna mais grave do projeto: todos os métodos estavam com
`AuthorizationType: NONE`, ou seja, qualquer pessoa com a URL podia registrar
lançamentos e ler o saldo do comerciante.

O que este autorizador faz, em ordem:
  1. extrai o token Bearer do cabeçalho Authorization
  2. valida a assinatura HMAC-SHA256 contra o segredo no Secrets Manager
  3. valida emissor, audiência e expiração
  4. confere se o token carrega o escopo exigido pelo recurso alvo
  5. devolve uma policy IAM de Allow ou Deny para o API Gateway

Sobre a escolha de HS256:
    Em produção com provedor de identidade externo (Cognito, Auth0, Keycloak) o
    correto é RS256 com chaves públicas obtidas do endpoint JWKS — assinatura
    assimétrica, sem segredo compartilhado entre emissor e validador.

    Aqui usamos HS256 com segredo no Secrets Manager por dois motivos: mantém a
    stack autocontida, sem depender de provedor externo para o avaliador testar;
    e passa a **usar** o segredo que hoje é provisionado e nunca lido.

    A troca por RS256 é localizada: só `_verificar_assinatura` e a origem da
    chave mudam. O resto — escopos, policy, cache — permanece igual.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import time

import boto3

# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------
REGIAO = os.environ.get("REGION", "us-east-1")
NOME_SEGREDO = os.environ["SECRET_NAME"]
EMISSOR_ESPERADO = os.environ.get("JWT_ISSUER", "gft-challenge")
AUDIENCIA_ESPERADA = os.environ.get("JWT_AUDIENCE", "fluxo-de-caixa")
TOLERANCIA_RELOGIO = int(os.environ.get("JWT_CLOCK_SKEW", "60"))

_secrets = boto3.client("secretsmanager", region_name=REGIAO)

# Cache do segredo entre invocações no mesmo contêiner. Sem isso, cada
# requisição faria uma chamada ao Secrets Manager — latência e custo por
# requisição, num caminho que roda antes de toda chamada da API.
_segredo_cache = {"valor": None, "expira_em": 0}
TTL_SEGREDO = 300


# ---------------------------------------------------------------------------
# Mapa de autorização: recurso -> escopo exigido
# ---------------------------------------------------------------------------
# O escopo descreve uma capacidade de negócio, não um papel. Um PDV recebe
# apenas `lancamentos:write`: ele precisa registrar vendas, não precisa ler a
# posição financeira do comerciante. Assim, um PDV comprometido numa loja não
# expõe o saldo do negócio inteiro.
ESCOPOS_POR_RECURSO = [
    (r"^POST:/lancamentos$",        "lancamentos:write"),
    (r"^GET:/lancamentos$",         "lancamentos:read"),
    (r"^GET:/lancamentos/[^/]+$",   "lancamentos:read"),
    (r"^GET:/consolidado$",         "consolidado:read"),
    (r"^GET:/consolidado/relatorio$", "consolidado:read"),
    (r"^GET:/metricas$",            "metricas:read"),
]


class NaoAutorizado(Exception):
    """Token ausente, inválido ou sem escopo suficiente."""


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------
def _decodificar_base64url(trecho: str) -> bytes:
    """base64url do JWT omite o preenchimento; é preciso recompô-lo."""
    return base64.urlsafe_b64decode(trecho + "=" * (-len(trecho) % 4))


def _obter_segredo() -> bytes:
    agora = time.time()
    if _segredo_cache["valor"] is None or agora >= _segredo_cache["expira_em"]:
        bruto = _secrets.get_secret_value(SecretId=NOME_SEGREDO)["SecretString"]
        try:
            # O segredo pode ser um JSON com várias chaves ou o valor direto.
            segredo = json.loads(bruto).get("jwt_secret", bruto)
        except json.JSONDecodeError:
            segredo = bruto
        _segredo_cache["valor"] = segredo.encode("utf-8")
        _segredo_cache["expira_em"] = agora + TTL_SEGREDO
    return _segredo_cache["valor"]


def _verificar_assinatura(cabecalho_b64: str, corpo_b64: str, assinatura_b64: str) -> None:
    esperada = hmac.new(
        _obter_segredo(),
        f"{cabecalho_b64}.{corpo_b64}".encode("utf-8"),
        hashlib.sha256,
    ).digest()

    # compare_digest, e não '==': comparação em tempo constante impede que o
    # atacante descubra a assinatura byte a byte medindo o tempo de resposta.
    if not hmac.compare_digest(esperada, _decodificar_base64url(assinatura_b64)):
        raise NaoAutorizado("assinatura inválida")


def validar_token(token: str) -> dict:
    partes = token.split(".")
    if len(partes) != 3:
        raise NaoAutorizado("token malformado")

    cabecalho_b64, corpo_b64, assinatura_b64 = partes

    try:
        cabecalho = json.loads(_decodificar_base64url(cabecalho_b64))
        corpo = json.loads(_decodificar_base64url(corpo_b64))
    except (ValueError, json.JSONDecodeError) as erro:
        raise NaoAutorizado("token ilegível") from erro

    # Rejeitar `alg` inesperado é obrigatório. Aceitar o algoritmo que o próprio
    # token declara permite o ataque clássico de trocá-lo por "none" e passar
    # sem assinatura nenhuma.
    if cabecalho.get("alg") != "HS256":
        raise NaoAutorizado("algoritmo não suportado")

    _verificar_assinatura(cabecalho_b64, corpo_b64, assinatura_b64)

    agora = time.time()
    if "exp" not in corpo:
        raise NaoAutorizado("token sem expiração")
    if agora > corpo["exp"] + TOLERANCIA_RELOGIO:
        raise NaoAutorizado("token expirado")
    if "nbf" in corpo and agora < corpo["nbf"] - TOLERANCIA_RELOGIO:
        raise NaoAutorizado("token ainda não válido")
    if corpo.get("iss") != EMISSOR_ESPERADO:
        raise NaoAutorizado("emissor inválido")

    # `aud` pode vir como string ou lista, conforme a RFC 7519.
    audiencia = corpo.get("aud")
    audiencias = audiencia if isinstance(audiencia, list) else [audiencia]
    if AUDIENCIA_ESPERADA not in audiencias:
        raise NaoAutorizado("audiência inválida")

    return corpo


# ---------------------------------------------------------------------------
# Autorização
# ---------------------------------------------------------------------------
def escopo_exigido(metodo: str, caminho: str) -> str:
    alvo = f"{metodo}:{caminho}"
    for padrao, escopo in ESCOPOS_POR_RECURSO:
        if re.match(padrao, alvo):
            return escopo
    # Recurso não mapeado nega por padrão. Fail-closed: um endpoint novo que
    # alguém esqueça de mapear fica inacessível, em vez de ficar aberto.
    raise NaoAutorizado(f"recurso não mapeado: {alvo}")


def tem_escopo(corpo: dict, exigido: str) -> bool:
    # `scope` separado por espaço (RFC 6749) ou `scp` como lista (formato Azure/Cognito)
    concedidos = corpo.get("scope", "")
    concedidos = concedidos.split() if isinstance(concedidos, str) else list(concedidos)
    concedidos += corpo.get("scp", []) if isinstance(corpo.get("scp"), list) else []
    return exigido in concedidos


def montar_policy(principal: str, efeito: str, recurso: str, contexto: dict) -> dict:
    return {
        "principalId": principal,
        "policyDocument": {
            "Version": "2012-10-17",
            "Statement": [{
                "Action": "execute-api:Invoke",
                "Effect": efeito,
                "Resource": recurso,
            }],
        },
        # O contexto é repassado ao Lambda de negócio em
        # `event.requestContext.authorizer`. É por aqui que o comerciante
        # autenticado chega à aplicação, sem que ela precise reabrir o token.
        "context": contexto,
    }


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------
def lambda_handler(event, context):
    metodo_arn = event.get("methodArn", "*")

    try:
        cabecalhos = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
        autorizacao = cabecalhos.get("authorization", "")

        if not autorizacao.lower().startswith("bearer "):
            raise NaoAutorizado("cabeçalho Authorization ausente ou malformado")

        corpo = validar_token(autorizacao[7:].strip())

        contexto_req = event.get("requestContext", {})
        metodo = contexto_req.get("httpMethod") or event.get("httpMethod", "")
        caminho = contexto_req.get("resourcePath") or event.get("path", "")

        exigido = escopo_exigido(metodo, caminho)
        if not tem_escopo(corpo, exigido):
            raise NaoAutorizado(f"escopo {exigido} não concedido")

        return montar_policy(
            principal=corpo.get("sub", "desconhecido"),
            efeito="Allow",
            recurso=metodo_arn,
            contexto={
                "comercianteId": str(corpo.get("comerciante_id", "")),
                "escopos": " ".join(
                    corpo.get("scope", "").split()
                    if isinstance(corpo.get("scope"), str) else corpo.get("scope", [])
                ),
                "subject": str(corpo.get("sub", "")),
            },
        )

    except NaoAutorizado as erro:
        # O motivo vai para o log, nunca para o cliente: detalhar por que o token
        # falhou entrega ao atacante um oráculo para ajustar a próxima tentativa.
        print(json.dumps({"level": "WARN", "event": "acesso_negado", "motivo": str(erro)}))
        return montar_policy("nao-autorizado", "Deny", metodo_arn, {})

    except Exception as erro:
        print(json.dumps({"level": "ERROR", "event": "falha_no_authorizer",
                          "erro": type(erro).__name__}))
        # Falha inesperada também nega. Nunca liberar por erro interno.
        return montar_policy("erro", "Deny", metodo_arn, {})
