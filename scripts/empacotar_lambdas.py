"""
Empacota as funções Lambda a partir do código-fonte.

Motivo de existir: os arquivos .zip estavam versionados no repositório. Isso
impede build reproduzível (não há garantia de que o .zip corresponda ao código
que está no git), esconde o que de fato foi implantado e polui o histórico com
binário. O pacote passa a ser gerado, não versionado.

Uso:
    python scripts/empacotar_lambdas.py            # gera em dist/
    python scripts/empacotar_lambdas.py --destino infra/lambdas
"""
import argparse
import hashlib
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
SERVICOS = ("lancamentos", "consolidado", "authorizer")

# Devem casar com o Runtime declarado em infra/cfn/lambdas.yml e com a
# arquitetura em que o Lambda executa.
RUNTIME_PYTHON = "3.10"
PLATAFORMA_LAMBDA = "manylinux2014_x86_64"

# Dependências por camada (layer). Mantidas fora do pacote da função para que o
# código da aplicação, que muda com frequência, suba sem rebaixar o cache das
# bibliotecas, que quase não mudam.
CAMADAS = {
    "xray": ["aws-xray-sdk==2.14.0"],
    # `async-timeout` é declarado explicitamente, e não deixado para o resolvedor.
    # O redis 5.x o exige com o marcador `python_full_version < "3.11.3"`; o
    # `--python-version 3.10` do pip não satisfaz marcadores de versão completa
    # de forma confiável, e a dependência sumia silenciosamente do pacote. O
    # runtime só acusava na primeira invocação, com ImportModuleError.
    "redis": ["redis==5.2.1", "async-timeout>=4.0.3"],
}


def empacotar_funcao(servico, destino):
    origem = RAIZ / "apps" / servico
    arquivo = destino / f"{servico}.zip"

    # Ordem estável e timestamp fixo: o mesmo código-fonte gera sempre o mesmo
    # .zip, então o hash serve como identidade da versão implantada.
    with zipfile.ZipFile(arquivo, "w", zipfile.ZIP_DEFLATED) as pacote:
        for py in sorted(origem.glob("*.py")):
            info = zipfile.ZipInfo(py.name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            pacote.writestr(info, py.read_bytes())

    return arquivo


def empacotar_camada(nome, pacotes, destino):
    arquivo = destino / f"{nome}.zip"

    with tempfile.TemporaryDirectory() as temporario:
        alvo = Path(temporario) / "python"
        resultado = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet",
             "--target", str(alvo),
             # A camada precisa refletir o ambiente do Lambda, não o da máquina
             # que empacota. Sem isto, dois defeitos aparecem só em produção:
             #
             # 1. Dependência condicional some. O `redis` declara `async-timeout`
             #    apenas para Python < 3.11; empacotando num 3.12+ o pip avalia a
             #    condição contra a versão local, não instala, e o runtime 3.10
             #    falha com ImportModuleError.
             # 2. Binário da plataforma errada. Pacotes com extensão compilada
             #    (wrapt, do aws-xray-sdk) trariam a build de Windows ou macOS
             #    para dentro de uma função que roda em Linux.
             "--python-version", RUNTIME_PYTHON,
             "--platform", PLATAFORMA_LAMBDA,
             # --python-version e --platform exigem que nada seja compilado na
             # hora: só wheels prontas para o alvo.
             "--only-binary=:all:",
             *pacotes],
            capture_output=True, text=True,
        )
        if resultado.returncode != 0:
            print(f"  [falha]   camada {nome}: {resultado.stderr.strip()[:200]}")
            return None

        with zipfile.ZipFile(arquivo, "w", zipfile.ZIP_DEFLATED) as pacote:
            for item in sorted(alvo.rglob("*")):
                if item.is_file() and "__pycache__" not in item.parts:
                    pacote.write(item, item.relative_to(alvo.parent))

    return arquivo


def resumo(arquivo):
    digest = hashlib.sha256(arquivo.read_bytes()).hexdigest()[:12]
    tamanho = arquivo.stat().st_size / 1024
    return f"{tamanho:>8.1f} KB  sha256:{digest}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destino", default="dist")
    parser.add_argument("--sem-camadas", action="store_true",
                        help="empacota apenas as funções, sem baixar dependências")
    args = parser.parse_args()

    destino = RAIZ / args.destino
    destino.mkdir(parents=True, exist_ok=True)

    print(f"\nEmpacotando em {destino.relative_to(RAIZ)}/\n")

    for servico in SERVICOS:
        arquivo = empacotar_funcao(servico, destino)
        print(f"  [função]  {servico + '.zip':<20} {resumo(arquivo)}")

    if not args.sem_camadas:
        for nome, pacotes in CAMADAS.items():
            arquivo = empacotar_camada(nome, pacotes, destino)
            if arquivo:
                print(f"  [camada]  {nome + '.zip':<20} {resumo(arquivo)}")

    print(f"""
Para implantar, envie os pacotes ao bucket usado pelo CloudFormation:

  aws s3 sync {args.destino}/ s3://<NOME_BUCKET>/infra/lambdas/
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
