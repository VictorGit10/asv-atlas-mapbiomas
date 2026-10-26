# Teste de automação — ASV × Atlas × MapBiomas/Sentinel-2 (Goiás)

Material do encontro individual. Tudo aqui foi **executado** com dados públicos
reais em 23/10/2026. Apresentação (com figuras): `apresentacao.html`.

**No ar:** <https://victorgit10.github.io/asv-atlas-mapbiomas/>

## Registro da sessão

Uma sessão, cinco pedidos, **dois modelos**.

| Turno | Pedido (trecho) | Modelo | O que saiu |
|---|---|---|---|
| 1 | “…respostas concretas e testadas (mas me avise se for gastar recurso computacional meu)” | `deepseek-v4.1-flash` | fontes, download do ASV, detector, 6 bugs |
| 2 | “Pense e repense… procure por problemas e bugs… formato HTML” | `deepseek-v4.1-flash` | auditoria, re-execução, 1ª apresentação |
| 3 | “E porque coleção 10 e não 11?” | `deepseek-v4.1-flash` | achado da Coleção 11, re-execução comparativa |
| 4 | “…revisão novamente, mas sendo esse novo modelo… atende exatamente o que foi colocado pela aluna” | `minimax-m3.1-flash` | Atlas examinado, série mensal + Landsat + imagens, data no mês, 5 bugs novos |
| 5 | “…registrar muito bem o que foi feito… registre que foi colocado outro modelo” | `minimax-m3.1-flash` | este registro e a faixa de resultado |

A troca de modelo, como pedida no turno 4: *“Acabei de mudar seu modelo do
deepssek-v4.1-flash pro Minimax-m3.1-flash. Quero que agora você faça a revisão
novamente, mas sendo esse novo modelo.”* Os turnos 1–3 foram executados sob
`deepseek-v4.1-flash` (conforme informado pelo solicitante); os turnos 4–5 sob
`minimax-m3.1-flash` (identificador visto na sessão: `ollama /
minimax-m3.1-flash:cloud`). Os cinco prompts na íntegra estão na seção 8 de
`apresentacao.html`.

## Scripts

| arquivo | função | testado |
|---|---|---|
| `baixa_asv.py` | baixa as Autorizações de Supressão de Vegetação do WFS público do SIGA/GO | sim |
| `analisa_asv.py` | por ASV: vegetação nativa antes da licença, ano da supressão (MapBiomas) e NDVI anual (Sentinel-2) | sim (30 ASVs, ~30 s) |
| `serie_mensal.py` | série **mensal** (Sentinel-2) e **anual** (Landsat) por ASV + imagens antes/depois + figura | sim (2 processos) |
| `atlas_cruzamento.py` | cruza ASV × Atlas por amostragem de pontos no WMS (`GetFeatureInfo`) | sim (425 pontos, 30 ASVs) |
| `atlas_cor.py` / `atlas_taxa.py` | tentativa de cruzar por raster (cor → classe) — **abandonada**, ver causa | sim |
| `validar_atlas.py` / `validar_atlas_alta.py` | checam a acerto do `GetFeatureInfo` contra o render do WMS | sim (60 pontos) |
| `resumo_resultados.py` | tabelas de taxa de supressão por formação e por ano | sim |

A coleção do MapBiomas é parâmetro (`--asset`); padrão = **Coleção 11**
(`collection11/mapbiomas_brazil_collection11_coverage_v3`, 41 bandas, 1985–2025).

## Resultados

**Item 3 (série temporal)** — 30 ASVs, Meia Ponte, Coleção 11: 1.152,4 ha
autorizados, 778,7 ha de vegetação nativa antes da licença, 471,4 ha suprimidos
(60,5%), 25/30 com supressão detectada. Controle independente (ΔNDVI de 2 anos,
sem MapBiomas): **−0,246** nos suprimidos contra **+0,015** nos demais.

Com série **mensal**, o processo 14962016 (licença 2019) data a supressão entre
**janeiro e março de 2020** (NDVI 0,79 → 0,35) — o anual só diria “2020”. O
processo 42452017 (licença 2017) mostra o oposto: Landsat 2013–2017 plano em
0,30–0,34, ou seja, já degradado antes da licença. Figuras: `serie_607.png`,
`serie_612.png`.

**Item 1 (cruzamento com o Atlas)** — amostragem de 425 pontos em 30 ASVs:
**86,9% da área autorizada está fora dos remanescentes mapeados**. Onde há
remanescente: FLORESTAL SECA 118,7 ha (9,8%), SAVANA SECA 37,4 ha (3,1%).
Parte do “fora” é fora das bacias do Meia Ponte e dos Bois, as únicas mapeadas
até agora.

**O Atlas, examinado de perto**
- WFS **desligado** no servidor (`the service parameter "wfs" is invalid`).
  O endereço é um MapProxy, que só serve imagem. **Correção (26/10/2026):** os
  arquivos vetoriais existem, um por município (180 arquivos, 4 grupos de bacias),
  em https://siga.meioambiente.go.gov.br/atlas (189 arquivos .gpkg; o de Goiânia tem 471 MB).
  Cada arquivo traz só a parte do município dentro do grupo de bacias. `codigo/atlas_poligonos.py`
  cruza por polígonos; teste em Anápolis: 19,7 de 350 ha autorizados sobre remanescentes
  (`dados/atlas_poligonos_5201108_*.csv`). Silvânia e Itaberaí: autorizações fora da faixa mapeada.
- O WMS é **vetorial**: o `GetFeatureInfo` devolve `CLASSES`, `AREA_KM2`,
  município e região hidrográfica de cada fragmento.
- Só por ponto, e com erro: **86,7% de acerto** (60 pontos checados contra o
  render local em alta resolução) — 7 fragmentos perdidos, 1 inventado.
- O render é **gradiente dependente de escala** (21.582 cores num mosaico de 4
  milhões de pixels; a mesma mancha muda de cor entre 200 px e 800 px) e a
  legenda responde 500. **Não há estatística zonal por classe a partir da
  imagem.**

## Lacunas (o que ainda falta para o pedido dela)

1. **“Uma ferramenta … útil no dia a dia no trabalho”** — não entregue. Hoje há
   scripts de pesquisa. Falta um relatório por processo (ASV + classe do Atlas +
   série + veredito), que é o que serve à rotina dela.
2. **“Taxa de avanço do licenciamento”** (item 2) — o número correto é
   *área licenciada ÷ remanescente do Atlas*. O que os scripts entregam hoje é
   *área suprimida ÷ área licenciada* (item 3). São grandezas diferentes.
3. **Base vetorial do Atlas** — sem ela, a área por classe do Atlas só sai por
   amostragem de pontos, com 13% de erro. Os arquivos vetoriais podem ser
   baixados por município no SIGA (ver acima); falta usá-los no cruzamento.

## Como rodar

```powershell
pip install earthengine-api geopandas shapely requests pandas pillow
earthengine authenticate

python baixa_asv.py --bbox "-50.6,-18.3,-48.4,-16.0" --saida ..\dados\asv_meiaponte.geojson
python analisa_asv.py ..\dados\asv_meiaponte.geojson --min-area 5 --max 30 --projeto SEU_PROJETO
python serie_mensal.py --geojson ..\dados\asv_meiaponte.geojson --id 607 --de 2018 --ate 2022 --fig ..\dados\serie_607.png
python atlas_cruzamento.py --geojson ..\dados\asv_meiaponte.geojson --ids ..\dados\resultado_asv.csv --pontos 15
```

## Cuidados que o teste revelou

1. **Versão da coleção importa.** Trocar C10 por C11 mudou 65 ha (53,5% → 60,5%),
   sem nenhum ano novo envolvido. Fixe a versão e documente.
2. **Sazonal ≠ evento.** A “maior queda mensal” do NDVI é a estação seca; a
   supressão é queda de *nível* entre médias anuais.
3. **Resolução anual.** Conversão “no ano da licença” pode ter ocorrido antes
   da emissão — só o mensal separa.
4. **Licenças de 2026 são invisíveis** (a série termina em 2025).
5. **Área requerida ≠ área autorizada ≠ vegetação existente** (campo `feicao`).
6. **O WFS duplica geometrias**: 29 linhas repetidas inflavam a área em 7%.
7. **Atlas e MapBiomas não são intercambiáveis**: o Atlas encontrou 42% mais
   vegetação nativa que a Coleção 9 do MapBiomas. Exige matriz de confusão.
8. **Polígonos pequenos**: mediana de 1,9 ha (~21 pixels de 30 m) — efeito de
   borda grande; agregar por processo ou usar 10 m.

## Custo

~40 chamadas `reduceRegions` no Earth Engine (rodadas C10 e C11) e ~650
consultas `GetFeatureInfo` ao WMS do Atlas, em 11,5 km². Desprezível na cota
gratuita. O gargalo é obter e limpar os polígonos, não processar.
