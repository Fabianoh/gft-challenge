"""
Registro de log sem vazamento de credencial.

O handler registrava o evento inteiro do API Gateway — cabeçalhos e corpo
incluídos. Com autenticação ativa, isso significa gravar o token `Bearer` em
texto claro no CloudWatch, onde ele fica retido por 30 dias e é lido por muito
mais gente do que se imagina.

A correção de uma lacuna criou a urgência da outra: antes da autenticação não
havia token para vazar. Ver docs/07-seguranca.md, ameaça T4.

O que se registra aqui é o suficiente para diagnosticar — método, recurso,
identificador de correlação, tamanho do corpo — sem nada que sirva para se
passar pelo cliente.
"""
import json

# Cabeçalhos que nunca podem ser registrados, mesmo truncados: um prefixo de
# token já reduz o espaço de busca de um atacante.
CABECALHOS_SENSIVEIS = {
    "authorization", "x-api-key", "cookie", "set-cookie",
    "x-amz-security-token", "proxy-authorization",
}


def resumir_evento(event: dict) -> str:
    """Extrai do evento apenas o que é seguro registrar."""
    contexto = event.get("requestContext") or {}
    cabecalhos = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    corpo = event.get("body") or ""

    resumo = {
        "metodo": event.get("httpMethod"),
        "recurso": event.get("resource"),
        "request_id": contexto.get("requestId"),
        # Correlação ponta a ponta sem expor conteúdo.
        "correlation_id": cabecalhos.get("x-correlation-id"),
        # Presença do cabeçalho, nunca o valor.
        "autenticado": "authorization" in cabecalhos,
        "tamanho_corpo": len(corpo),
        "origem": (contexto.get("identity") or {}).get("sourceIp"),
    }

    # A identidade autenticada vem do authorizer, já validada — é atributo de
    # negócio, não credencial, e é o que permite auditar quem fez o quê.
    autorizador = contexto.get("authorizer") or {}
    if autorizador.get("comercianteId"):
        resumo["comerciante_id"] = autorizador["comercianteId"]
    if autorizador.get("subject"):
        resumo["subject"] = autorizador["subject"]

    return json.dumps({k: v for k, v in resumo.items() if v is not None},
                      default=str, ensure_ascii=False)


def resumir_evento_sqs(event: dict) -> str:
    """Equivalente para eventos de fila: conta e identificadores, não conteúdo."""
    registros = event.get("Records") or []
    return json.dumps({
        "origem": "sqs",
        "mensagens": len(registros),
        "message_ids": [r.get("messageId") for r in registros[:10]],
    }, default=str, ensure_ascii=False)
