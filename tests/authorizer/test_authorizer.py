"""
Testes do Lambda Authorizer.

Cobrem o caminho feliz, os ataques clássicos contra validação de JWT e o
mapeamento de escopos por recurso.

Princípio que orienta a suíte: **negar por padrão**. Todo caso de dúvida — token
ausente, algoritmo inesperado, recurso não mapeado, erro interno — precisa
resultar em Deny. Um authorizer que libera em caso de dúvida é pior que não ter
authorizer, porque cria falsa sensação de proteção.
"""
import time

import pytest
from main import lambda_handler


def efeito(resposta):
    return resposta["policyDocument"]["Statement"][0]["Effect"]


class TestCaminhoFeliz:
    def test_token_valido_com_escopo_correto_e_permitido(self, token, evento):
        resposta = lambda_handler(evento(token()), None)

        assert efeito(resposta) == "Allow"
        assert resposta["principalId"] == "pdv-loja-042"

    def test_comerciante_e_repassado_no_contexto(self, token, evento):
        """A aplicação recebe o comerciante autenticado sem reabrir o token —
        é o que permitirá o isolamento multi-tenant pela chave."""
        resposta = lambda_handler(evento(token()), None)

        assert resposta["context"]["comercianteId"] == "COM#4711"
        assert resposta["context"]["subject"] == "pdv-loja-042"

    def test_policy_aponta_para_o_recurso_solicitado(self, token, evento):
        ev = evento(token())
        resposta = lambda_handler(ev, None)

        assert resposta["policyDocument"]["Statement"][0]["Resource"] == ev["methodArn"]


class TestAusenciaDeCredencial:
    def test_sem_cabecalho_authorization_e_negado(self, evento):
        assert efeito(lambda_handler(evento(None), None)) == "Deny"

    def test_cabecalho_sem_prefixo_bearer_e_negado(self, evento, token):
        ev = evento(token())
        ev["headers"]["Authorization"] = ev["headers"]["Authorization"][7:]

        assert efeito(lambda_handler(ev, None)) == "Deny"

    def test_token_vazio_e_negado(self, evento):
        ev = evento("x")
        ev["headers"]["Authorization"] = "Bearer "

        assert efeito(lambda_handler(ev, None)) == "Deny"

    @pytest.mark.parametrize("lixo", ["abc", "a.b", "a.b.c.d", "...", "não-é-jwt"])
    def test_token_malformado_e_negado(self, evento, lixo):
        assert efeito(lambda_handler(evento(lixo), None)) == "Deny"


class TestAtaquesConhecidos:
    def test_algoritmo_none_e_rejeitado(self, token, evento):
        """Ataque clássico: trocar `alg` para "none" e remover a assinatura.
        Bibliotecas que confiam no algoritmo declarado pelo próprio token
        aceitam qualquer conteúdo. O authorizer exige HS256 explicitamente."""
        resposta = lambda_handler(evento(token(alg="none", assinar=False)), None)

        assert efeito(resposta) == "Deny"

    def test_assinatura_com_segredo_errado_e_rejeitada(self, token, evento):
        resposta = lambda_handler(evento(token(segredo="segredo-do-atacante")), None)

        assert efeito(resposta) == "Deny"

    def test_corpo_adulterado_invalida_a_assinatura(self, token, evento, corpo_valido):
        """Elevar o próprio escopo editando o corpo quebra a assinatura."""
        import base64
        import json

        original = token()
        cabecalho_b64, _, assinatura_b64 = original.split(".")
        adulterado = dict(corpo_valido, scope="lancamentos:write consolidado:read admin:*")
        corpo_b64 = base64.urlsafe_b64encode(
            json.dumps(adulterado).encode()).decode().rstrip("=")

        resposta = lambda_handler(
            evento(f"{cabecalho_b64}.{corpo_b64}.{assinatura_b64}"), None
        )

        assert efeito(resposta) == "Deny"

    def test_token_expirado_e_rejeitado(self, token, evento):
        agora = int(time.time())
        resposta = lambda_handler(evento(token(exp=agora - 7200, iat=agora - 10800)), None)

        assert efeito(resposta) == "Deny"

    def test_token_sem_expiracao_e_rejeitado(self, evento, corpo_valido):
        """Token sem `exp` seria válido para sempre — inaceitável."""
        from conftest import montar_token

        sem_exp = {k: v for k, v in corpo_valido.items() if k != "exp"}

        assert efeito(lambda_handler(evento(montar_token(sem_exp)), None)) == "Deny"

    def test_token_de_outro_emissor_e_rejeitado(self, token, evento):
        resposta = lambda_handler(evento(token(iss="emissor-desconhecido")), None)

        assert efeito(resposta) == "Deny"

    def test_token_para_outra_audiencia_e_rejeitado(self, token, evento):
        """Impede que um token emitido para outro sistema da mesma organização
        seja reaproveitado nesta API."""
        resposta = lambda_handler(evento(token(aud="outro-sistema")), None)

        assert efeito(resposta) == "Deny"

    def test_token_ainda_nao_valido_e_rejeitado(self, token, evento):
        agora = int(time.time())
        resposta = lambda_handler(evento(token(nbf=agora + 7200)), None)

        assert efeito(resposta) == "Deny"

    def test_tolerancia_de_relogio_aceita_expiracao_recentissima(self, token, evento):
        """Diferença de relógio entre emissor e validador não pode derrubar
        cliente legítimo. A tolerância padrão é de 60 segundos."""
        agora = int(time.time())
        resposta = lambda_handler(evento(token(exp=agora - 10)), None)

        assert efeito(resposta) == "Allow"


class TestAutorizacaoPorEscopo:
    @pytest.mark.parametrize("metodo,caminho,escopo", [
        ("POST", "/lancamentos", "lancamentos:write"),
        ("GET", "/lancamentos", "lancamentos:read"),
        ("GET", "/lancamentos/550e8400-e29b-41d4-a716-446655440000", "lancamentos:read"),
        ("GET", "/consolidado", "consolidado:read"),
        ("GET", "/consolidado/relatorio", "consolidado:read"),
        ("GET", "/metricas", "metricas:read"),
    ])
    def test_escopo_suficiente_permite(self, token, evento, metodo, caminho, escopo):
        resposta = lambda_handler(evento(token(scope=escopo), metodo, caminho), None)

        assert efeito(resposta) == "Allow"

    def test_pdv_com_escopo_de_escrita_nao_le_o_saldo(self, token, evento):
        """O caso que justifica autorização por escopo: um PDV comprometido
        numa loja não expõe a posição financeira do negócio inteiro."""
        somente_escrita = token(scope="lancamentos:write")

        assert efeito(lambda_handler(
            evento(somente_escrita, "POST", "/lancamentos"), None)) == "Allow"
        assert efeito(lambda_handler(
            evento(somente_escrita, "GET", "/consolidado"), None)) == "Deny"

    def test_escopo_de_leitura_nao_permite_escrever(self, token, evento):
        resposta = lambda_handler(
            evento(token(scope="lancamentos:read"), "POST", "/lancamentos"), None
        )

        assert efeito(resposta) == "Deny"

    def test_token_sem_nenhum_escopo_e_negado(self, token, evento):
        assert efeito(lambda_handler(evento(token(scope="")), None)) == "Deny"

    def test_formato_scp_em_lista_e_aceito(self, token, evento, corpo_valido):
        """Cognito e Azure AD emitem escopos em `scp` como lista."""
        from conftest import montar_token

        corpo = {k: v for k, v in corpo_valido.items() if k != "scope"}
        corpo["scp"] = ["lancamentos:write"]

        resposta = lambda_handler(evento(montar_token(corpo)), None)

        assert efeito(resposta) == "Allow"

    def test_recurso_nao_mapeado_e_negado(self, token, evento):
        """Fail-closed: um endpoint novo que alguém esqueça de mapear fica
        inacessível, em vez de ficar aberto."""
        resposta = lambda_handler(evento(token(), "DELETE", "/lancamentos"), None)

        assert efeito(resposta) == "Deny"


class TestVazamentoDeInformacao:
    def test_resposta_de_negacao_nao_revela_o_motivo(self, token, evento):
        """Detalhar por que o token falhou entrega ao atacante um oráculo para
        ajustar a próxima tentativa."""
        resposta = lambda_handler(evento(token(segredo="errado")), None)

        serializado = str(resposta).lower()
        for pista in ("assinatura", "expirado", "emissor", "escopo", "audiência"):
            assert pista not in serializado

    def test_falha_interna_nega_em_vez_de_liberar(self, evento, token, monkeypatch):
        """Erro inesperado nunca pode resultar em Allow."""
        import main

        monkeypatch.setattr(main, "validar_token",
                            lambda _: (_ for _ in ()).throw(RuntimeError("falha")))

        assert efeito(lambda_handler(evento(token()), None)) == "Deny"
