# Painel no Power BI: guia de montagem

Os CSVs desta pasta são gerados por `python src/pipeline.py` e já estão no formato de modelo estrela.

## 1. Importar

**Obter dados → Texto/CSV** e importe todos os arquivos desta pasta, mais `outputs/cmv_mensal_por_prato.csv` (renomeie a tabela para `fato_cmv_mensal`). Em cada tabela, deixe a coluna `mes` como **Texto** e `data` como **Data**.

## 2. Relacionamentos (Exibição de modelo)

| De (muitos) | Para (um) | Coluna |
|---|---|---|
| fato_preco_insumo | dim_insumo | id_insumo |
| fato_preco_insumo | dim_calendario | mes |
| fato_ficha_tecnica | dim_insumo | id_insumo |
| fato_ficha_tecnica | dim_prato | id_prato |
| fato_cmv_mensal | dim_prato | id_prato |
| fato_cmv_mensal | dim_calendario | mes |
| fato_previsao_cmv | dim_prato | id_prato |
| fato_previsao_cmv | dim_calendario | mes |

## 3. Medidas DAX

```DAX
CMV (R$) = SUM ( fato_cmv_mensal[cmv] )

Preço de Venda (R$) = SUM ( fato_cmv_mensal[preco_venda] )

CMV % = DIVIDE ( [CMV (R$)], [Preço de Venda (R$)] )

Margem Bruta (R$) = [Preço de Venda (R$)] - [CMV (R$)]

CMV 12m Atrás (R$) =
CALCULATE ( [CMV (R$)], DATEADD ( dim_calendario[data], -12, MONTH ) )

Variação CMV 12m % = DIVIDE ( [CMV (R$)] - [CMV 12m Atrás (R$)], [CMV 12m Atrás (R$)] )

CMV % 12m Atrás =
CALCULATE ( [CMV %], DATEADD ( dim_calendario[data], -12, MONTH ) )

Δ CMV % (p.p.) = ( [CMV %] - [CMV % 12m Atrás] ) * 100

CMV Previsto (R$) = SUM ( fato_previsao_cmv[cmv_previsto] )

Preço Médio Insumo (R$) = AVERAGE ( fato_preco_insumo[preco] )
```

> Para `DATEADD` funcionar, marque `dim_calendario` como **tabela de datas** (coluna `data`).

## 4. Layout sugerido (1 página)

1. **Cartões**: CMV % atual · Variação CMV 12m % · Δ CMV % (p.p.) · Margem Bruta (R$)
2. **Linhas**: `CMV %` por `data`, legenda `prato`
3. **Barras horizontais**: `outputs/impacto_insumos_12m.csv` (impacto em R$ por insumo)
4. **Linhas + previsão**: `CMV (R$)` e `CMV Previsto (R$)` por `data`
5. **Segmentações**: prato, categoria do insumo, ano

Cores do projeto: azul `#1A56A0`, laranja `#E07B39`, verde `#2E9E6B`, roxo `#8E5BB5`, vermelho `#C94C4C`.
