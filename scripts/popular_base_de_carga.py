"""
Popula a tabela de lançamentos para que o teste de carga meça algo relevante.

Sem isto, o cenário k6 roda contra uma tabela quase vazia e passa
trivialmente — uma medição verdadeira de um cenário irrelevante.

Por que o volume importa:
    O k6 consulta datas aleatórias que não têm saldo pré-calculado. Cada
    requisição cai no caminho caro — `get_saldo_diario` não encontra nada,
    dispara `calculate_saldo_diario`, que faz `scan` da tabela inteira e grava o
    resultado. O custo desse scan é proporcional ao tamanho da tabela, que é
    exatamente a degradação prevista no ADR-004.

Escreve direto no DynamoDB, em lote, e não pela API: é ~50x mais rápido, não
consome cota do API Gateway e não polui a métrica de carga com o tráfego de
preparação.

Uso:
    python scripts/popular_base_de_carga.py --tabela <nome> --quantidade 20000
"""
import argparse
import random
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import boto3

CATEGORIAS = ["VENDAS", "SERVICOS", "FORNECEDORES", "FOLHA", "IMPOSTOS", "GERAL"]


def gerar_lancamento(dia_base: datetime, ambiente: str) -> dict:
    """Gera um lançamento verossímil: valores e tipos com distribuição realista."""
    # 70% créditos: um comerciante saudável recebe mais do que paga.
    tipo = "CREDITO" if random.random() < 0.70 else "DEBITO"
    valor = Decimal(str(round(random.uniform(5, 2500), 2)))
    instante = dia_base + timedelta(
        hours=random.randint(8, 21), minutes=random.randint(0, 59)
    )
    return {
        "id": str(uuid.uuid4()),
        "tipo": tipo,
        "valor": valor,
        "descricao": f"Movimento {tipo.lower()} de carga",
        "data": instante.isoformat(),
        "categoria": random.choice(CATEGORIAS),
        "tags": [],
        "data_criacao": instante.isoformat(),
        "data_atualizacao": instante.isoformat(),
        "status": "ATIVO",
        "ambiente": ambiente,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tabela", required=True)
    parser.add_argument("--quantidade", type=int, default=20000)
    parser.add_argument("--dias", type=int, default=28,
                        help="espalha os lançamentos por N dias de julho/2025, "
                             "que é o período consultado pelo cenário k6")
    parser.add_argument("--regiao", default="us-east-1")
    parser.add_argument("--ambiente", default="dev")
    args = parser.parse_args()

    tabela = boto3.resource("dynamodb", region_name=args.regiao).Table(args.tabela)

    print(f"\nPopulando {args.tabela}")
    print(f"  {args.quantidade} lançamentos em {args.dias} dias de julho/2025\n")

    inicio = time.time()
    gravados = 0

    # batch_writer agrupa em lotes de 25 e trata retentativa de itens não
    # processados automaticamente.
    with tabela.batch_writer() as lote:
        for i in range(args.quantidade):
            dia = datetime(2025, 7, 1, tzinfo=timezone.utc) + timedelta(
                days=random.randint(0, args.dias - 1)
            )
            lote.put_item(Item=gerar_lancamento(dia, args.ambiente))
            gravados += 1
            if gravados % 2000 == 0:
                decorrido = time.time() - inicio
                print(f"  {gravados:>6} gravados  ({gravados/decorrido:.0f}/s)")

    decorrido = time.time() - inicio
    print(f"\n  {gravados} lançamentos em {decorrido:.0f}s "
          f"({gravados/decorrido:.0f}/s)")
    print("\n  A tabela agora tem volume suficiente para que o scan do")
    print("  recálculo custe algo mensurável. Rode o teste de carga.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
