#!/usr/bin/env python
"""
Executor de tarefas do projeto — equivalente ao Makefile, para qualquer sistema.

O Makefile cobre Linux e macOS. No Windows, `make` não vem instalado, e exigir
que o avaliador instale um utilitário só para rodar o projeto seria atrito
desnecessário. Este script usa apenas a biblioteca padrão.

    python tarefas.py                  lista as tarefas
    python tarefas.py instalar
    python tarefas.py subir
    python tarefas.py rodar
    python tarefas.py testar
"""
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
VENV = RAIZ / ".venv"
PYTHON = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

SERVICOS = ("lancamentos", "consolidado", "authorizer")

TAREFAS = {}


def tarefa(nome, descricao):
    def registrar(funcao):
        funcao.descricao = descricao
        TAREFAS[nome] = funcao
        return funcao
    return registrar


def python():
    """Interpretador do venv, caindo para o atual se o venv não existir."""
    return str(PYTHON) if PYTHON.exists() else sys.executable


def executar(*argumentos, **extras):
    resultado = subprocess.run([python(), *argumentos], cwd=RAIZ, **extras)
    return resultado.returncode


# ---------------------------------------------------------------------------
@tarefa("instalar", "Cria o ambiente virtual e instala as dependências")
def instalar():
    if not VENV.exists():
        subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True)
    executar("-m", "pip", "install", "--quiet", "--upgrade", "pip")
    codigo = executar("-m", "pip", "install", "--quiet", "-r", "requirements-dev.txt")
    if codigo == 0:
        print("Ambiente pronto. Próximo passo: python tarefas.py subir")
    return codigo


@tarefa("testar", "Roda todas as suítes de teste")
def testar():
    # Um processo por serviço: eles têm módulos de mesmo nome e colidiriam
    # em sys.modules. Ver tests/conftest.py.
    falhas = 0
    for servico in SERVICOS:
        print(f"\n--- suíte: {servico} ---")
        falhas |= executar("-m", "pytest", f"tests/{servico}")
    return falhas


@tarefa("cobertura", "Roda os testes com relatório de cobertura")
def cobertura():
    falhas = 0
    for servico in ("lancamentos", "consolidado"):
        print(f"\n--- cobertura: {servico} ---")
        falhas |= executar("-m", "pytest", f"tests/{servico}",
                           f"--cov=apps/{servico}", "--cov-report=term-missing")
    return falhas


@tarefa("lacunas", "Mostra as lacunas conhecidas do MVP, cobertas por teste")
def lacunas():
    for servico in ("lancamentos", "consolidado"):
        executar("-m", "pytest", f"tests/{servico}/test_lacunas_conhecidas.py", "-rx")
    return 0


@tarefa("subir", "Sobe a infra local emulada e cria tabelas e filas")
def subir():
    if _emulador_no_ar():
        print("Emulador já está no ar em http://localhost:5000")
    else:
        print("Subindo emulador AWS na porta 5000...")
        registro = open(RAIZ / ".moto.log", "w")
        subprocess.Popen([python(), "-m", "moto.server", "-p", "5000"],
                         cwd=RAIZ, stdout=registro, stderr=registro)
        for _ in range(30):
            if _emulador_no_ar():
                break
            time.sleep(1)
        else:
            print("Emulador não respondeu. Veja .moto.log")
            return 1

    return executar("scripts/preparar_ambiente_local.py")


@tarefa("rodar", "Sobe a API local em http://localhost:3000")
def rodar():
    if not _emulador_no_ar():
        print("A infra local não está no ar. Rode antes: python tarefas.py subir")
        return 1
    return executar("scripts/api_local.py")


@tarefa("derrubar", "Encerra a infra local")
def derrubar():
    if os.name == "nt":
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        "Get-CimInstance Win32_Process -Filter \"Name like '%python%'\" |"
                        " Where-Object { $_.CommandLine -like '*moto.server*' -or"
                        " $_.CommandLine -like '*api_local*' } |"
                        " ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"],
                       capture_output=True)
    else:
        subprocess.run(["pkill", "-f", "moto.server"], capture_output=True)
        subprocess.run(["pkill", "-f", "api_local"], capture_output=True)

    if shutil.which("docker"):
        subprocess.run(["docker", "compose", "down", "-v"], cwd=RAIZ,
                       capture_output=True)

    (RAIZ / ".moto.log").unlink(missing_ok=True)
    print("Ambiente local encerrado.")
    return 0


@tarefa("empacotar", "Empacota as funções Lambda a partir do código-fonte")
def empacotar():
    # Gera direto em infra/lambdas/, que é o caminho esperado pelos templates.
    # Assim o `aws s3 sync` da raiz envia templates e pacotes de uma vez.
    # Os .zip são ignorados pelo git: versionar binário impede build reproduzível.
    return executar("scripts/empacotar_lambdas.py", "--destino", "infra/lambdas")


@tarefa("carga", "Teste de carga de 50 rps (requer k6 e a variável API_URL)")
def carga():
    url = os.environ.get("API_URL")
    if not url:
        print("Defina API_URL. Exemplo:\n"
              "  API_URL=https://abc.execute-api.us-east-1.amazonaws.com/prod "
              "python tarefas.py carga")
        return 1
    if not shutil.which("k6"):
        print("k6 não encontrado. Instale em https://k6.io/docs/get-started/installation/")
        return 1
    return subprocess.run(["k6", "run", "-e", f"API_URL={url}",
                           "tests/carga/consolidado-50rps.js"], cwd=RAIZ).returncode


@tarefa("limpar", "Remove artefatos gerados")
def limpar():
    for alvo in (".pytest_cache", ".coverage", "htmlcov", "dist"):
        caminho = RAIZ / alvo
        if caminho.is_dir():
            shutil.rmtree(caminho, ignore_errors=True)
        elif caminho.exists():
            caminho.unlink()
    for cache in RAIZ.rglob("__pycache__"):
        if ".venv" not in cache.parts:
            shutil.rmtree(cache, ignore_errors=True)
    print("Artefatos removidos.")
    return 0


# ---------------------------------------------------------------------------
def _emulador_no_ar():
    try:
        urllib.request.urlopen("http://localhost:5000", timeout=1)
        return True
    except urllib.error.HTTPError:
        return True   # respondeu, ainda que com erro HTTP — está no ar
    except Exception:
        return False


def ajuda():
    print("\n  Tarefas disponíveis:\n")
    for nome, funcao in TAREFAS.items():
        print(f"    {nome:<12} {funcao.descricao}")
    print("\n  Uso: python tarefas.py <tarefa>\n")
    return 0


if __name__ == "__main__":
    escolhida = sys.argv[1] if len(sys.argv) > 1 else None
    if escolhida in TAREFAS:
        sys.exit(TAREFAS[escolhida]() or 0)
    if escolhida:
        print(f"Tarefa desconhecida: {escolhida}")
    sys.exit(ajuda())
