"""
Configuração da suíte do Lambda Authorizer.

Mesmo padrão dos outros serviços: ambiente falso e mocks AWS antes de qualquer
import da aplicação, porque o módulo lê configuração em tempo de import.
"""
import base64
import hashlib
import hmac
import json
import os
import sys
import time
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
APP = RAIZ / "apps" / "authorizer"
sys.path.insert(0, str(APP))

SEGREDO = "segredo-de-teste-nao-usar-em-producao"
EMISSOR = "gft-challenge"
AUDIENCIA = "fluxo-de-caixa"

os.environ.update({
    "AWS_ACCESS_KEY_ID": "testing",
    "AWS_SECRET_ACCESS_KEY": "testing",
    "AWS_SECURITY_TOKEN": "testing",
    "AWS_SESSION_TOKEN": "testing",
    "AWS_DEFAULT_REGION": "us-east-1",
    "AWS_XRAY_SDK_ENABLED": "false",
    "REGION": "us-east-1",
    "SECRET_NAME": "gft-test-jwt",
    "JWT_ISSUER": EMISSOR,
    "JWT_AUDIENCE": AUDIENCIA,
})

from moto import mock_aws  # noqa: E402

_mock = mock_aws()
_mock.start()

import boto3  # noqa: E402

boto3.client("secretsmanager", region_name="us-east-1").create_secret(
    Name="gft-test-jwt",
    SecretString=json.dumps({"jwt_secret": SEGREDO}),
)


# ---------------------------------------------------------------------------
# Fábrica de tokens
# ---------------------------------------------------------------------------
def _b64(dado: bytes) -> str:
    return base64.urlsafe_b64encode(dado).decode().rstrip("=")


def montar_token(corpo: dict, segredo: str = SEGREDO, alg: str = "HS256",
                 assinar: bool = True) -> str:
    """Monta um JWT à mão, para poder produzir tokens deliberadamente inválidos."""
    cabecalho_b64 = _b64(json.dumps({"alg": alg, "typ": "JWT"}).encode())
    corpo_b64 = _b64(json.dumps(corpo).encode())

    if not assinar:
        return f"{cabecalho_b64}.{corpo_b64}."

    assinatura = hmac.new(
        segredo.encode(), f"{cabecalho_b64}.{corpo_b64}".encode(), hashlib.sha256
    ).digest()
    return f"{cabecalho_b64}.{corpo_b64}.{_b64(assinatura)}"


@pytest.fixture
def corpo_valido():
    agora = int(time.time())
    return {
        "sub": "pdv-loja-042",
        "iss": EMISSOR,
        "aud": AUDIENCIA,
        "iat": agora,
        "exp": agora + 3600,
        "scope": "lancamentos:write lancamentos:read consolidado:read metricas:read",
        "comerciante_id": "COM#4711",
    }


@pytest.fixture
def token(corpo_valido):
    """Gera um token a partir do corpo padrão, com alterações pontuais."""
    def _gerar(**alteracoes):
        opcoes = {k: alteracoes.pop(k) for k in ("segredo", "alg", "assinar")
                  if k in alteracoes}
        return montar_token(dict(corpo_valido, **alteracoes), **opcoes)
    return _gerar


@pytest.fixture
def evento():
    """Monta o evento que o API Gateway envia ao authorizer (tipo REQUEST)."""
    def _construir(token=None, metodo="POST", caminho="/lancamentos"):
        cabecalhos = {"Authorization": f"Bearer {token}"} if token else {}
        return {
            "type": "REQUEST",
            "methodArn": f"arn:aws:execute-api:us-east-1:123456789012:abc/prod/{metodo}{caminho}",
            "headers": cabecalhos,
            "requestContext": {"httpMethod": metodo, "resourcePath": caminho},
        }
    return _construir


@pytest.fixture(autouse=True)
def limpar_cache_do_segredo():
    """O segredo é cacheado entre invocações; zera entre testes."""
    import main
    main._segredo_cache["valor"] = None
    main._segredo_cache["expira_em"] = 0
    yield
