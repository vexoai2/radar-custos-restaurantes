-- Radar de Custos para Restaurantes: consultas de CMV
-- Tabelas carregadas pelo pipeline (src/pipeline.py):
--   insumos(id_insumo, insumo, categoria, codigo_sidra, unidade, preco_ref_out2026)
--   fichas_tecnicas(id_prato, prato, id_insumo, quantidade)
--   pratos(id_prato, prato, preco_venda_out2026)
--   preco_insumo_mensal(mes, id_insumo, preco)        -- preço corrigido pelo IPCA do subitem
--   preco_venda_mensal(mes, id_prato, preco_venda)    -- preço corrigido pelo IPCA "Refeição"

-- name: cmv_mensal
-- CMV de cada prato mês a mês e % do CMV sobre o preço de venda
SELECT
    p.mes,
    f.id_prato,
    f.prato,
    ROUND(SUM(f.quantidade * p.preco), 2)                      AS cmv,
    ROUND(v.preco_venda, 2)                                    AS preco_venda,
    ROUND(SUM(f.quantidade * p.preco) / v.preco_venda * 100, 2) AS cmv_pct
FROM fichas_tecnicas f
JOIN preco_insumo_mensal p ON p.id_insumo = f.id_insumo
JOIN preco_venda_mensal  v ON v.id_prato = f.id_prato AND v.mes = p.mes
GROUP BY p.mes, f.id_prato, f.prato, v.preco_venda
ORDER BY f.id_prato, p.mes;

-- name: composicao_cmv_atual
-- Peso de cada insumo no CMV de cada prato no último mês
WITH ultimo AS (SELECT MAX(mes) AS mes FROM preco_insumo_mensal)
SELECT
    f.prato,
    i.insumo,
    ROUND(f.quantidade * p.preco, 4) AS custo_insumo,
    ROUND(f.quantidade * p.preco * 100.0
          / SUM(f.quantidade * p.preco) OVER (PARTITION BY f.id_prato), 2) AS peso_no_cmv_pct
FROM fichas_tecnicas f
JOIN insumos i             ON i.id_insumo = f.id_insumo
JOIN preco_insumo_mensal p ON p.id_insumo = f.id_insumo
JOIN ultimo u              ON u.mes = p.mes
ORDER BY f.prato, custo_insumo DESC;

-- name: impacto_insumo_12m
-- Quanto cada insumo somou (ou tirou) do CMV total dos 5 pratos em 12 meses, em R$ por porção
WITH ref AS (
    SELECT MAX(mes) AS fim,
           (SELECT mes FROM (SELECT DISTINCT mes FROM preco_insumo_mensal ORDER BY mes DESC LIMIT 1 OFFSET 12)) AS inicio
    FROM preco_insumo_mensal
)
SELECT
    i.insumo,
    i.categoria,
    ROUND(SUM(f.quantidade * (pf.preco - pi.preco)), 4)  AS impacto_cmv_reais,
    ROUND((MAX(pf.preco) / MAX(pi.preco) - 1) * 100, 2)  AS variacao_preco_12m_pct
FROM fichas_tecnicas f
JOIN insumos i ON i.id_insumo = f.id_insumo
JOIN ref
JOIN preco_insumo_mensal pf ON pf.id_insumo = f.id_insumo AND pf.mes = ref.fim
JOIN preco_insumo_mensal pi ON pi.id_insumo = f.id_insumo AND pi.mes = ref.inicio
GROUP BY i.insumo, i.categoria
ORDER BY impacto_cmv_reais DESC;
