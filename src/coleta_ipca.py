"""Coleta a variação mensal do IPCA (IBGE/SIDRA, tabela 7060) dos insumos do projeto.

Uso:
    python src/coleta_ipca.py

Saída: data/raw/ipca_subitens_mensal.csv
Colunas: mes (AAAAMM), codigo_sidra, subitem, variacao_mensal_pct
"""
from pathlib import Path

import pandas as pd
import requests

RAIZ = Path(__file__).resolve().parents[1]
SAIDA = RAIZ / "data" / "raw" / "ipca_subitens_mensal.csv"

# Tabela 7060: IPCA a partir de jan/2020 | Variável 63: variação mensal (%)
# Classificação 315: geral, grupo, subgrupo, item e subitem
TABELA, VARIAVEL, CLASSIF = 7060, 63, 315

# Referências para comparação: índice geral e "Refeição" (alimentação fora do domicílio)
REFERENCIAS = [7169, 7434]


def codigos_insumos() -> list[int]:
    insumos = pd.read_csv(RAIZ / "data" / "insumos.csv")
    return insumos["codigo_sidra"].astype(int).tolist()


def coletar(codigos: list[int]) -> pd.DataFrame:
    lista = ",".join(str(c) for c in codigos)
    url = (
        f"https://apisidra.ibge.gov.br/values/t/{TABELA}/n1/all/v/{VARIAVEL}"
        f"/p/all/c{CLASSIF}/{lista}/f/a"
    )
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    dados = resp.json()

    df = pd.DataFrame(dados[1:])  # a primeira linha da API é o cabeçalho
    df = df.rename(columns={
        "D3C": "mes",
        "D4C": "codigo_sidra",
        "D4N": "subitem",
        "V": "variacao_mensal_pct",
    })[["mes", "codigo_sidra", "subitem", "variacao_mensal_pct"]]

    # A API usa "..." / "-" para valores ausentes
    df["variacao_mensal_pct"] = pd.to_numeric(df["variacao_mensal_pct"], errors="coerce")
    df["codigo_sidra"] = df["codigo_sidra"].astype(int)
    return df.sort_values(["codigo_sidra", "mes"]).reset_index(drop=True)


def main() -> None:
    df = coletar(REFERENCIAS + codigos_insumos())
    faltando = df["variacao_mensal_pct"].isna().sum()
    SAIDA.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(SAIDA, index=False, encoding="utf-8")
    print(f"{len(df)} linhas | {df['codigo_sidra'].nunique()} séries | "
          f"{df['mes'].min()} a {df['mes'].max()} | valores ausentes: {faltando}")


if __name__ == "__main__":
    main()
