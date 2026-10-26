"""
Analisa Autorizações de Supressão de Vegetação (ASV) do SIGA/GO no Earth Engine.

Para cada polígono autorizado responde:
  1. quanta vegetação nativa existia ali no ano da licença (MapBiomas C10);
  2. se essa vegetação foi de fato suprimida e em que ano (transição anual
     vegetação nativa -> uso antrópico, série 1985-2025);
  3. como o NDVI (Sentinel-2) se comportou antes e depois da supressão.

Custo: uma chamada reduceRegions por ano distinto (não por polígono) a 30 m.
Rodar 30 polígonos levou ~30 s e consumiu uma fração ínfima da cota gratuita.

Uso:
    python analisa_asv.py ../dados/asv_meiaponte.geojson --min-area 5 --max 30
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import ee
import pandas as pd
from shapely import force_2d
from shapely.geometry import mapping

# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

MB_ASSET = (
    "projects/mapbiomas-public/assets/brazil/lulc/collection11/"
    "mapbiomas_brazil_collection11_coverage_v3"
)

# Classes do MapBiomas consideradas vegetação nativa (Coleção 11)
NATURAL = [3, 4, 5, 6, 11, 12, 32, 49, 50]

# Classes consideradas uso antrópico (perda efetiva de vegetação nativa)
ANTROPICO = [9, 15, 19, 20, 21, 24, 30, 31, 35, 36, 39, 40, 41, 46, 47, 48, 62, 75]

NOME_CLASSE = {
    3: "Formação Florestal", 4: "Formação Savânica", 5: "Mangue",
    6: "Floresta Alagável", 9: "Silvicultura", 11: "Campo Alagado e Área Pantanosa",
    12: "Formação Campestre", 15: "Pastagem", 19: "Lavoura Temporária", 20: "Cana",
    21: "Mosaico de Usos", 24: "Área Urbanizada", 25: "Outras Áreas não Vegetadas",
    29: "Afloramento Rochoso", 30: "Mineração", 31: "Aquicultura", 32: "Apicum",
    33: "Rio, Lago e Oceano", 35: "Dendê", 36: "Lavoura Perene", 39: "Soja",
    40: "Arroz", 41: "Outras Lavouras Temporárias", 46: "Café", 47: "Citrus",
    48: "Outras Lavouras Perenes", 49: "Restinga Arbórea", 50: "Restinga Herbácea",
    62: "Algodão", 75: "Usina Fotovoltaica",
}

ANOS_MB: list[int] = []      # preenchido a partir das bandas do asset
ANO_FINAL = 0                # último ano da série (fim de série)
SEM_CONVERSAO = 9999


def carrega_serie(asset: str) -> None:
    """Lê as bandas do asset e descobre o intervalo de anos disponível."""
    global MB_ASSET, ANOS_MB, ANO_FINAL
    MB_ASSET = asset
    bandas = ee.Image(asset).bandNames().getInfo()
    ANOS_MB = sorted(int(b.rsplit("_", 1)[-1]) for b in bandas)
    ANO_FINAL = ANOS_MB[-1]
    print(f"série: {ANOS_MB[0]}–{ANO_FINAL} ({len(ANOS_MB)} bandas)")


def inicializa(projeto: str, credencial: str | None = None) -> None:
    """Inicializa o Earth Engine por conta de serviço (ou credencial padrão)."""
    if credencial:
        with open(credencial, encoding="utf-8") as fh:
            email = json.load(fh)["client_email"]
        ee.Initialize(ee.ServiceAccountCredentials(email, credencial), project=projeto)
    else:
        ee.Initialize(project=projeto)


def geometria_ee(geom, tolerancia_m: float = 10.0) -> ee.Geometry:
    """Converte shapely (WGS84) -> ee.Geometry 2D, simplificando em metros."""
    if not geom.is_valid:
        geom = geom.buffer(0)
    graus = tolerancia_m / 111_320  # ~1 grau de latitude
    return ee.Geometry(mapping(force_2d(geom.simplify(graus))))


# ---------------------------------------------------------------------------
# Imagens derivadas
# ---------------------------------------------------------------------------

def imagem_conversao() -> ee.Image:
    """
    Ano em que cada pixel deixou de ser vegetação nativa e virou uso antrópico
    (1986 até o fim da série). 9999 = nunca converteu.

    A transição é detectada par a par (natural em t-1 e antrópico em t), então
    área que já era pastagem em 1985 não conta como supressão nova.
    """
    stack = ee.Image(MB_ASSET)

    def mascara(classes: list[int], prefixo: str) -> ee.Image:
        return ee.Image.cat(
            [
                stack.select(f"classification_{a}")
                .remap(classes, [1] * len(classes), 0)
                .rename(f"{prefixo}_{a}")
                for a in ANOS_MB
            ]
        )

    natural = mascara(NATURAL, "nat")
    antropico = mascara(ANTROPICO, "ant")

    bandas = []
    for i, ano in enumerate(ANOS_MB[1:], start=1):
        conv = natural.select(f"nat_{ANOS_MB[i - 1]}").multiply(
            antropico.select(f"ant_{ano}")
        )
        bandas.append(
            conv.multiply(ano).add(conv.eq(0).multiply(SEM_CONVERSAO)).rename(f"c{ano}")
        )
    return ee.Image.cat(bandas).reduce(ee.Reducer.min()).rename("ano_conversao")


def classe_no_ano(ano: int) -> ee.Image:
    return ee.Image(MB_ASSET).select(f"classification_{ano}").rename("classe")


def ndvi_anual(ano: int, geometria: ee.Geometry) -> ee.Image:
    """NDVI mediano do ano (Sentinel-2 SR Harmonized, máscara de nuvem por SCL)."""
    col = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(geometria)
        .filterDate(f"{ano}-01-01", f"{ano + 1}-01-01")
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 60))
    )

    def limpa(img):
        scl = img.select("SCL")
        ok = scl.neq(3).And(scl.neq(8)).And(scl.neq(9)).And(scl.neq(10)).And(scl.neq(11))
        return img.updateMask(ok).normalizedDifference(["B8", "B4"]).rename(f"ndvi_{ano}")

    return col.map(limpa).median().rename(f"ndvi_{ano}")


# ---------------------------------------------------------------------------
# Estatísticas por polígono (uma chamada por ano, não por polígono)
# ---------------------------------------------------------------------------

def histograma_por_feicao(img: ee.Image, feicoes: ee.FeatureCollection) -> dict:
    """Área (ha) por valor de pixel dentro de cada polígono."""
    area = ee.Image.pixelArea().divide(10000).addBands(img)
    res = area.reduceRegions(
        collection=feicoes,
        reducer=ee.Reducer.sum().group(groupField=1, groupName="classe"),
        scale=30,
        tileScale=4,
    )
    return {
        str(f["properties"].get("id")): {
            int(g["classe"]): float(g["sum"]) for g in (f["properties"].get("groups") or [])
        }
        for f in res.getInfo()["features"]
    }


def media_por_feicao(img: ee.Image, feicoes: ee.FeatureCollection) -> dict:
    res = img.reduceRegions(
        collection=feicoes, reducer=ee.Reducer.mean(), scale=30, tileScale=4
    )
    return {
        str(f["properties"].get("id")): f["properties"].get("mean")
        for f in res.getInfo()["features"]
    }


# ---------------------------------------------------------------------------
# Principal
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("geojson", type=Path)
    ap.add_argument("--min-area", type=float, default=5.0, help="área mínima (ha)")
    ap.add_argument("--max", type=int, default=60, help="máximo de polígonos")
    ap.add_argument("--ano-min", type=int, default=2018)
    ap.add_argument("--ano-max", type=int, default=2024)
    ap.add_argument("--asset", default=MB_ASSET, help="asset MapBiomas (coleção/versão)")
    ap.add_argument("--projeto", default="extreme-height-447417-a9")
    ap.add_argument("--credencial", default=None)
    ap.add_argument("--saida", type=Path, default=Path("../dados/resultado_asv.csv"))
    args = ap.parse_args()

    inicializa(args.projeto, args.credencial)
    carrega_serie(args.asset)

    import geopandas as gpd

    gdf = gpd.read_file(args.geojson).to_crs(31982)
    gdf["area_ha"] = gdf.area / 10000
    gdf["ano_licenca"] = (
        pd.to_datetime(gdf["dataemissaolicenca"], utc=True, errors="coerce")
        .dt.year.fillna(pd.to_datetime(gdf["datacriacao"], utc=True, errors="coerce").dt.year)
    )

    sel = gdf[
        (gdf.area_ha >= args.min_area)
        & gdf.ano_licenca.between(args.ano_min, args.ano_max)
        & gdf.geometry.notna()
        & ~gdf.geometry.is_empty
    ].copy()

    # o WFS publica a mesma geometria mais de uma vez (atividade A1.1.1 etc.)
    sel = sel.drop_duplicates(subset=["numero_processo", "area_ha"])

    if len(sel) > args.max:
        partes = []
        for _, grupo in sel.groupby("ano_licenca"):
            n = max(1, round(len(grupo) * args.max / len(sel)))
            partes.append(grupo.sample(min(n, len(grupo)), random_state=42))
        sel = pd.concat(partes).sample(frac=1, random_state=42).head(args.max)

    print(f"polígonos: {len(sel)} | área autorizada: {sel.area_ha.sum():,.1f} ha")

    sel_wgs = sel.to_crs(4326)
    feicoes = ee.FeatureCollection(
        [
            ee.Feature(geometria_ee(sel_wgs.loc[i].geometry), {"id": str(int(i))})
            for i in sel.index
        ]
    )

    # 1) área convertida, por ano de conversão (uma chamada)
    hist_conv = histograma_por_feicao(imagem_conversao(), feicoes)

    # 2) classe no ano da licença e no fim da série (uma chamada por ano distinto)
    anos_base = sorted(sel.ano_licenca.dropna().astype(int).unique())
    anos_lic = sorted(set(anos_base) | {a - 1 for a in anos_base})
    hist_lic = {a: histograma_por_feicao(classe_no_ano(a), feicoes) for a in anos_lic}
    hist_final = histograma_por_feicao(classe_no_ano(ANO_FINAL), feicoes)

    # 3) NDVI anual (uma chamada por ano)
    anos_ndvi = list(range(max(2017, int(sel.ano_licenca.min()) - 2), 2026))
    ndvi = {a: media_por_feicao(ndvi_anual(a, feicoes.geometry()), feicoes) for a in anos_ndvi}

    # -----------------------------------------------------------------------
    # Consolidação
    # -----------------------------------------------------------------------
    registros = []
    for idx, row in sel.iterrows():
        fid = str(int(idx))
        ano_lic = int(row.ano_licenca)

        classe_l = hist_lic[ano_lic - 1].get(fid, {})
        classe_f = hist_final.get(fid, {})
        area_nativa_base = sum(v for k, v in classe_l.items() if k in NATURAL)
        area_nativa_2024 = sum(v for k, v in classe_f.items() if k in NATURAL)
        area_antrop_2024 = sum(v for k, v in classe_f.items() if k in ANTROPICO)

        conv = {
            int(a): v
            for a, v in hist_conv.get(fid, {}).items()
            if int(a) != SEM_CONVERSAO and v >= 0.05
        }
        pos_licenca = {a: v for a, v in conv.items() if a >= ano_lic}
        ano_conv_ever = min(conv) if conv else None
        ano_conv_pos = min(pos_licenca) if pos_licenca else None
        area_conv_pos = sum(pos_licenca.values())

        def ndvi_medio(anos: list[int]) -> float | None:
            vals = [ndvi[a].get(fid) for a in anos if a in ndvi and ndvi[a].get(fid) is not None]
            return round(sum(vals) / len(vals), 3) if vals else None

        antes = ndvi_medio([ano_lic - 2, ano_lic - 1])
        depois = ndvi_medio([ano_conv_pos, ano_conv_pos + 1]) if ano_conv_pos else None

        dominante = max(classe_l.items(), key=lambda kv: kv[1])[0] if classe_l else None

        registros.append(
            {
                "id": fid,
                "processo": row.get("numero_processo"),
                "municipio": row.get("municipioibge"),
                "ano_licenca": ano_lic,
                "area_autorizada_ha": round(row.area_ha, 2),
                "classe_dominante": NOME_CLASSE.get(dominante, dominante),
                "nat_ha_antes_da_licenca": round(area_nativa_base, 2),
                "nat_ha_fim_serie": round(area_nativa_2024, 2),
                "antrop_ha_fim_serie": round(area_antrop_2024, 2),
                "ano_1a_conversao": ano_conv_ever,
                "ano_conversao_pos_licenca": ano_conv_pos,
                "area_conv_pos_licenca_ha": round(area_conv_pos, 2),
                'pct_veg_autorizada_suprimida': (round(100 * area_conv_pos / area_nativa_base, 1) if area_nativa_base > 0.05 else None),
                "ndvi_antes": antes,
                "ndvi_depois": depois,
                "delta_ndvi": round(depois - antes, 3) if antes and depois else None,
                **{f"ndvi_{a}": ndvi[a].get(fid) for a in anos_ndvi},
            }
        )

    df = pd.DataFrame(registros).sort_values("area_autorizada_ha", ascending=False)
    args.saida.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.saida, index=False, encoding="utf-8-sig")

    # -----------------------------------------------------------------------
    # Resumo
    # -----------------------------------------------------------------------
    com_veg = df[df.nat_ha_antes_da_licenca > 0.05]
    suprimidas = com_veg[com_veg.ano_conversao_pos_licenca.notna()]
    print(f"\npolígonos com vegetação nativa na licença: {len(com_veg)}")
    print(f"com supressão detectada após a licença: {len(suprimidas)}")
    print(f"área autorizada total: {df.area_autorizada_ha.sum():,.1f} ha")
    print(f"área nativa antes da licença: {com_veg.nat_ha_antes_da_licenca.sum():,.1f} ha")
    print(f"área nativa suprimida depois da licença: {df.area_conv_pos_licenca_ha.sum():,.1f} ha")
    if len(suprimidas):
        print(f"delta NDVI mediano (antes-depois): {suprimidas.delta_ndvi.median():.3f}")
        print(f"defasagem mediana licença->supressão (anos): "
              f"{(suprimidas.ano_conversao_pos_licenca - suprimidas.ano_licenca).median():.0f}")
    print(f"\nsalvo em {args.saida.resolve()}")
    print(df.head(12).to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
