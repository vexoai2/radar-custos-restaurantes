"""Radar de Custos para Restaurantes: pipeline completo.

Etapas:
  1. Trata o IPCA mensal dos subitens e monta um número-índice por insumo
  2. Converte o índice em preço (R$) a partir do preço de referência de out/2026
  3. Calcula CMV por prato em SQL (SQLite) e % do CMV sobre o preço de venda
  4. Prevê os próximos 3 meses (método escolhido por backtest)
  5. Exporta as tabelas para o Power BI e gera os gráficos do README

Uso:
    python src/coleta_ipca.py   # opcional: atualiza os dados do IBGE
    python src/pipeline.py
"""
import re
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

RAIZ = Path(__file__).resolve().parents[1]
DATA, SQL, OUT, IMG, PBI = (RAIZ / p for p in ("data", "sql", "outputs", "img", "powerbi"))
COD_REFEICAO, COD_GERAL = 7434, 7169
HORIZONTE = 3


# ---------------------------------------------------------------- 1. tratamento
def carregar():
    ipca = pd.read_csv(DATA / "raw" / "ipca_subitens_mensal.csv", dtype={"mes": str})
    insumos = pd.read_csv(DATA / "insumos.csv")
    fichas = pd.read_csv(DATA / "fichas_tecnicas.csv")
    pratos = pd.read_csv(DATA / "pratos.csv")

    var = ipca.pivot(index="mes", columns="codigo_sidra", values="variacao_mensal_pct").sort_index()
    assert var.notna().all().all(), "Há meses sem variação na série do IPCA"
    return var, insumos, fichas, pratos


def indice(var: pd.DataFrame) -> pd.DataFrame:
    """Número-índice (dez/2019 = 1) a partir das variações mensais (%)."""
    return (1 + var / 100).cumprod()


def precos_mensais(idx, insumos, pratos):
    ref = idx.index.max()
    p_ins = []
    for _, r in insumos.iterrows():
        s = r.preco_ref_out2026 * idx[r.codigo_sidra] / idx.loc[ref, r.codigo_sidra]
        p_ins.append(pd.DataFrame({"mes": s.index, "id_insumo": r.id_insumo, "preco": s.values}))
    p_ins = pd.concat(p_ins, ignore_index=True)

    s_ref = idx[COD_REFEICAO] / idx.loc[ref, COD_REFEICAO]
    p_venda = pd.concat([
        pd.DataFrame({"mes": s_ref.index, "id_prato": r.id_prato, "preco_venda": r.preco_venda_out2026 * s_ref.values})
        for _, r in pratos.iterrows()
    ], ignore_index=True)
    return p_ins, p_venda


# ---------------------------------------------------------------- 2. SQL
def consultas_sql() -> dict[str, str]:
    texto = (SQL / "cmv.sql").read_text(encoding="utf-8")
    blocos = re.split(r"^-- name: (\w+)\s*$", texto, flags=re.M)
    return {blocos[i]: blocos[i + 1] for i in range(1, len(blocos), 2)}


def rodar_sql(tabelas: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    con = sqlite3.connect(":memory:")
    for nome, df in tabelas.items():
        df.to_sql(nome, con, index=False)
    res = {nome: pd.read_sql_query(q, con) for nome, q in consultas_sql().items()}
    con.close()
    return res


# ---------------------------------------------------------------- 3. previsão
def prever_variacoes(var: pd.DataFrame, metodo: str, h: int = HORIZONTE) -> pd.DataFrame:
    """Prevê as variações mensais (%) dos próximos h meses para cada série."""
    ultimo = pd.Period(var.index.max(), freq="M")
    futuros = [(ultimo + k) for k in range(1, h + 1)]
    mes_cal = pd.PeriodIndex(var.index, freq="M").month
    linhas = {}
    for p in futuros:
        if metodo == "sem_variacao":
            linhas[p.strftime("%Y%m")] = pd.Series(0.0, index=var.columns)
        elif metodo == "media_12m":
            linhas[p.strftime("%Y%m")] = var.tail(12).mean()
        elif metodo == "sazonal_mediana":
            linhas[p.strftime("%Y%m")] = var[mes_cal == p.month].median()
        else:
            raise ValueError(metodo)
    return pd.DataFrame(linhas).T


METODOS = ["sem_variacao", "media_12m", "sazonal_mediana"]


def cmv_de_indices(idx_fim: pd.Series, idx_ini: pd.Series, insumos, fichas) -> pd.Series:
    """Variação (%) do CMV de cada prato entre dois vetores de índice."""
    f = fichas.merge(insumos, on="id_insumo")
    f["custo_ini"] = f.quantidade * f.preco_ref_out2026 * f.codigo_sidra.map(idx_ini)
    f["custo_fim"] = f.quantidade * f.preco_ref_out2026 * f.codigo_sidra.map(idx_fim)
    g = f.groupby("prato")[["custo_ini", "custo_fim"]].sum()
    return (g.custo_fim / g.custo_ini - 1) * 100


def backtest(var, insumos, fichas, n_origens: int = 24) -> pd.DataFrame:
    """Erro médio absoluto (p.p.) da variação do CMV em 3 meses, por método."""
    idx = indice(var)
    erros = {m: [] for m in METODOS}
    meses = list(var.index)
    for o in range(len(meses) - HORIZONTE - n_origens, len(meses) - HORIZONTE):
        hist = var.iloc[: o + 1]
        real = cmv_de_indices(idx.iloc[o + HORIZONTE], idx.iloc[o], insumos, fichas)
        for m in METODOS:
            prev_var = prever_variacoes(hist, m)
            idx_prev = idx.iloc[o] * (1 + prev_var / 100).prod()
            prev = cmv_de_indices(idx_prev, idx.iloc[o], insumos, fichas)
            erros[m].extend((prev - real).abs().tolist())
    return pd.DataFrame({"metodo": METODOS, "erro_medio_pp": [np.mean(erros[m]) for m in METODOS]}) \
        .sort_values("erro_medio_pp").reset_index(drop=True)


# ---------------------------------------------------------------- 4. gráficos
def graficos(cmv, impacto, acum, prev_cmv):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "axes.spines.top": False,
                         "axes.spines.right": False, "svg.fonttype": "none"})
    cores = ["#1A56A0", "#E07B39", "#2E9E6B", "#8E5BB5", "#C94C4C"]
    IMG.mkdir(exist_ok=True)

    # 1. CMV % do preço de venda ao longo do tempo
    fig, ax = plt.subplots(figsize=(9, 4.2))
    for (prato, g), c in zip(cmv.groupby("prato", sort=False), cores):
        ax.plot(pd.to_datetime(g.mes, format="%Y%m"), g.cmv_pct, label=prato, color=c, lw=1.8)
    ax.set_title("CMV como % do preço de venda (preço reajustado pelo IPCA Refeição)", loc="left")
    ax.set_ylabel("% do preço")
    ax.legend(frameon=False, fontsize=8, ncol=2)
    fig.tight_layout(); fig.savefig(IMG / "cmv_pct_pratos.svg"); plt.close(fig)

    # 2. Inflação acumulada dos insumos vs Refeição
    a = acum.sort_values("acumulado_desde_2020_pct")
    fig, ax = plt.subplots(figsize=(9, 6.5))
    cor = ["#C94C4C" if v > a.loc[a.insumo == "Refeição (IPCA)", "acumulado_desde_2020_pct"].iloc[0] else "#9AA5B1"
           for v in a.acumulado_desde_2020_pct]
    cor = ["#1A56A0" if n == "Refeição (IPCA)" else c for n, c in zip(a.insumo, cor)]
    ax.barh(a.insumo, a.acumulado_desde_2020_pct, color=cor)
    ax.set_title("Inflação acumulada jan/2020 – out/2026 (%)\nVermelho: subiu mais que o preço da refeição", loc="left")
    for y, v in enumerate(a.acumulado_desde_2020_pct):
        ax.text(v + (2 if v >= 0 else -2), y, f"{v:.0f}%", va="center", ha="left" if v >= 0 else "right", fontsize=8)
    fig.tight_layout(); fig.savefig(IMG / "inflacao_acumulada_insumos.svg"); plt.close(fig)

    # 3. Impacto de cada insumo no CMV em 12 meses
    i = impacto.sort_values("impacto_cmv_reais")
    fig, ax = plt.subplots(figsize=(9, 6.5))
    ax.barh(i.insumo, i.impacto_cmv_reais, color=["#C94C4C" if v > 0 else "#2E9E6B" for v in i.impacto_cmv_reais])
    ax.axvline(0, color="#333", lw=0.8)
    ax.set_title("Quanto cada insumo mudou o CMV dos 5 pratos em 12 meses (R$ por porção)", loc="left")
    fig.tight_layout(); fig.savefig(IMG / "impacto_insumos_12m.svg"); plt.close(fig)

    # 4. Previsão
    fig, ax = plt.subplots(figsize=(9, 4.2))
    for (prato, g), c in zip(cmv.groupby("prato", sort=False), cores):
        h = g.tail(12)
        ax.plot(pd.to_datetime(h.mes, format="%Y%m"), h.cmv, color=c, lw=1.8, label=prato)
        p = prev_cmv[prev_cmv.prato == prato]
        xs = pd.to_datetime([h.mes.iloc[-1]] + p.mes.tolist(), format="%Y%m")
        ax.plot(xs, [h.cmv.iloc[-1]] + p.cmv_previsto.tolist(), color=c, lw=1.8, ls="--")
    ax.set_title("CMV por porção (R$): últimos 12 meses e previsão de 3 meses (tracejado)", loc="left")
    ax.legend(frameon=False, fontsize=8, ncol=2)
    fig.tight_layout(); fig.savefig(IMG / "previsao_cmv.svg"); plt.close(fig)


# ---------------------------------------------------------------- main
def main():
    var, insumos, fichas, pratos = carregar()
    idx = indice(var)
    p_ins, p_venda = precos_mensais(idx, insumos, pratos)

    res = rodar_sql({"insumos": insumos, "fichas_tecnicas": fichas, "pratos": pratos,
                     "preco_insumo_mensal": p_ins, "preco_venda_mensal": p_venda})
    cmv, composicao, impacto = res["cmv_mensal"], res["composicao_cmv_atual"], res["impacto_insumo_12m"]

    # inflação acumulada por insumo
    nomes = dict(zip(insumos.codigo_sidra, insumos.insumo)) | {COD_REFEICAO: "Refeição (IPCA)", COD_GERAL: "IPCA geral"}
    ult = idx.index.max(); m12 = idx.index[-13]
    acum = pd.DataFrame({
        "codigo_sidra": idx.columns,
        "insumo": [nomes[c] for c in idx.columns],
        "acumulado_desde_2020_pct": ((idx.loc[ult] - 1) * 100).round(2).values,
        "acumulado_12m_pct": ((idx.loc[ult] / idx.loc[m12] - 1) * 100).round(2).values,
    }).sort_values("acumulado_12m_pct", ascending=False)

    # previsão
    bt = backtest(var, insumos, fichas)
    metodo = bt.metodo.iloc[0]
    prev_var = prever_variacoes(var, metodo)
    idx_prev = idx.iloc[-1] * (1 + prev_var / 100).cumprod()
    f = fichas.merge(insumos, on="id_insumo")
    prev_rows = []
    for mes, linha in idx_prev.iterrows():
        f["c"] = f.quantidade * f.preco_ref_out2026 * f.codigo_sidra.map(linha / idx.iloc[-1])
        for prato, v in f.groupby("prato", sort=False)["c"].sum().items():
            prev_rows.append({"mes": mes, "prato": prato, "cmv_previsto": round(v, 4)})
    prev_cmv = pd.DataFrame(prev_rows)
    atual = cmv[cmv.mes == ult][["prato", "cmv", "preco_venda", "cmv_pct"]]
    prev_cmv = prev_cmv.merge(atual[["prato", "cmv", "preco_venda"]], on="prato")
    prev_cmv["variacao_vs_out2026_pct"] = ((prev_cmv.cmv_previsto / prev_cmv.cmv - 1) * 100).round(2)
    prev_cmv = prev_cmv.drop(columns=["cmv", "preco_venda"])

    # exportações
    OUT.mkdir(exist_ok=True)
    cmv.to_csv(OUT / "cmv_mensal_por_prato.csv", index=False)
    composicao.to_csv(OUT / "composicao_cmv_out2026.csv", index=False)
    impacto.to_csv(OUT / "impacto_insumos_12m.csv", index=False)
    acum.to_csv(OUT / "inflacao_acumulada.csv", index=False)
    bt.to_csv(OUT / "backtest_previsao.csv", index=False)
    prev_cmv.to_csv(OUT / "previsao_cmv_3m.csv", index=False)

    # modelo estrela para o Power BI
    PBI.mkdir(exist_ok=True)
    meses = pd.DataFrame({"mes": list(idx.index) + list(idx_prev.index)})
    dt = pd.to_datetime(meses.mes, format="%Y%m")
    meses["data"] = dt.dt.strftime("%Y-%m-%d"); meses["ano"] = dt.dt.year; meses["mes_num"] = dt.dt.month
    meses["tipo"] = ["real"] * len(idx) + ["previsto"] * len(idx_prev)
    meses.to_csv(PBI / "dim_calendario.csv", index=False)
    insumos.to_csv(PBI / "dim_insumo.csv", index=False)
    pratos.to_csv(PBI / "dim_prato.csv", index=False)
    fichas[["id_prato", "id_insumo", "quantidade"]].to_csv(PBI / "fato_ficha_tecnica.csv", index=False)
    p_ins.assign(preco=p_ins.preco.round(2)).to_csv(PBI / "fato_preco_insumo.csv", index=False)
    prev_cmv.merge(pratos[["id_prato", "prato"]], on="prato").to_csv(PBI / "fato_previsao_cmv.csv", index=False)

    graficos(cmv, impacto, acum[acum.insumo != "IPCA geral"], prev_cmv)

    # resumo no terminal
    ini = cmv[cmv.mes == m12].set_index("prato"); fim = cmv[cmv.mes == ult].set_index("prato")
    print(f"Período: {idx.index.min()} a {ult} | método de previsão: {metodo}")
    print(bt.to_string(index=False))
    print(pd.DataFrame({"cmv_12m_antes": ini.cmv, "cmv_atual": fim.cmv,
                        "var_cmv_12m_pct": ((fim.cmv / ini.cmv - 1) * 100).round(2),
                        "cmv_pct_antes": ini.cmv_pct, "cmv_pct_atual": fim.cmv_pct}).to_string())


if __name__ == "__main__":
    main()
