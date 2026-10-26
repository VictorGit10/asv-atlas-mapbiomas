"""
Série temporal fina (item 3): data a supressão no mês e traz as imagens.

- Sentinel-2 SR Harmonized: NDVI mensal (2017+) com máscara de nuvem por SCL.
- Landsat Collection 2 L2 (L5/L7/L8/L9): NDVI anual para a janela anterior a 2017.
- MapBiomas: ano de conversão (chega do CSV do análise).

Uso:
    python serie_mensal.py --geojson ../dados/asv_meiaponte.geojson --id 607 --fig ../dados/serie_607.png
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

import ee
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import geopandas as gpd
import pandas as pd
import numpy as np
import pandas as pd
import requests
from shapely import force_2d
from shapely.geometry import mapping

MB_ASSET = ("projects/mapbiomas-public/assets/brazil/lulc/collection11/"
            "mapbiomas_brazil_collection11_coverage_v3")


def s2_mensal(ano_ini: int, ano_fim: int, roi: ee.Geometry) -> ee.Image:
    """NDVI mediano mensal do Sentinel-2, uma banda por mês."""
    col = ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED").filterBounds(roi)

    def limpa(img):
        scl = img.select("SCL")
        ok = scl.neq(3).And(scl.neq(8)).And(scl.neq(9)).And(scl.neq(10)).And(scl.neq(11))
        return img.updateMask(ok).normalizedDifference(["B8", "B4"])

    bandas, rotulos = [], []
    for ano in range(ano_ini, ano_fim + 1):
        for mes in range(1, 13):
            m = col.filterDate(ee.Date.fromYMD(ano, mes, 1), ee.Date.fromYMD(ano, mes, 1).advance(1, "month"))
            n = m.size().getInfo()
            if n == 0:
                continue
            bandas.append(m.map(limpa).median().rename(f"{ano}-{mes:02d}"))
            rotulos.append(f"{ano}-{mes:02d}")
    return ee.Image.cat(bandas)


def landsat_anual(anos: list[int], roi: ee.Geometry) -> ee.Image:
    """NDVI anual do Landsat C2 L2 (L5/L7/L8/L9 harmonizados)."""
    bandas = []
    for ano in anos:
        cols = []
        for asset in ["LANDSAT/LT05/C02/T1_L2", "LANDSAT/LE07/C02/T1_L2",
                      "LANDSAT/LC08/C02/T1_L2", "LANDSAT/LC09/C02/T1_L2"]:
            c = (ee.ImageCollection(asset).filterBounds(roi)
                 .filterDate(f"{ano}-01-01", f"{ano + 1}-01-01")
                 .filter(ee.Filter.lt("CLOUD_COVER", 40)))
            if c.size().getInfo() == 0:
                continue
            cols.append(c)
        if not cols:
            continue

        col = ee.ImageCollection([])
        for c in cols:
            col = col.merge(c)

        def escala(i):
            return i.addBands(i.select("SR_B.").multiply(0.0000275).add(-0.2), None, True)

        def limpa(i):
            # bit 4 = cloud, bit 1 = dilated cloud
            qa = i.select("QA_PIXEL")
            ok = qa.bitwiseAnd(int("10000", 2)).eq(0).And(qa.bitwiseAnd(int("10000", 2)).eq(0))
            return i.normalizedDifference(["SR_B5", "SR_B4"]).updateMask(ok)

        img = col.map(escala).map(limpa).median().rename(f"ndvi_{ano}")
        bandas.append(img)
    return ee.Image.cat(bandas)


def rgb(ano: int, roi: ee.Geometry, largura: int = 700) -> ee.Image:
    """Composto RGB mediano do ano, com o limite do polígono."""
    col = (ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
           .filterBounds(roi)
           .filterDate(f"{ano}-01-01", f"{ano + 1}-01-01")
           .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 20)))
    img = col.median().select(["B4", "B3", "B2"])
    rgb = img.visualize(bands=["B4", "B3", "B2"], min=0, max=3000)
    contorno = ee.Image().byte().paint(ee.FeatureCollection([ee.Feature(roi)]), 1, 2)
    contorno3 = ee.Image.cat([contorno, contorno, contorno])
    return rgb.blend(contorno3).clip(roi.buffer(300).bounds())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--geojson", type=Path, required=True)
    ap.add_argument("--id", required=True, help="índice do polígono no GeoJSON")
    ap.add_argument("--processo", default=None)
    ap.add_argument("--ano-licenca", type=int, default=2017)
    ap.add_argument("--de", type=int, default=2017)
    ap.add_argument("--ate", type=int, default=2025)
    ap.add_argument("--fig", type=Path, default=Path("../dados/serie.png"))
    ap.add_argument("--credencial", default=None)
    ap.add_argument("--projeto", default="extreme-height-447417-a9")
    args = ap.parse_args()

    if args.credencial:
        email = json.load(open(args.credencial, encoding="utf-8"))["client_email"]
        ee.Initialize(ee.ServiceAccountCredentials(email, args.credencial), project=args.projeto)
    else:
        ee.Initialize(project=args.projeto)

    g = gpd.read_file(args.geojson)
    row = g.loc[int(args.id)]
    lic = pd.to_datetime(row.get("dataemissaolicenca"), utc=True, errors="coerce")
    ano_lic = int(lic.year) if pd.notna(lic) else args.ano_licenca
    geom = force_2d(row.geometry.simplify(5))
    roi = ee.Geometry(mapping(geom))
    print(f"processo {row.get('numero_processo')} | licença {ano_lic} | bbox {np.round(row.geometry.bounds, 4).tolist()}")

    # --- Landsat anual (janela pré-2017) e Sentinel-2 mensal
    anos_landsat = list(range(2013, max(2017, ano_lic) + 1))
    landsat = landsat_anual(anos_landsat, roi)
    lstats = landsat.reduceRegions(collection=ee.FeatureCollection([ee.Feature(roi)]),
                                   reducer=ee.Reducer.mean(), scale=30, tileScale=4).getInfo()
    lvals = {k: v for k, v in (lstats["features"][0]["properties"]).items() if k.startswith("ndvi_")}

    mensal = s2_mensal(args.de, args.ate, roi)
    mstats = mensal.reduceRegions(collection=ee.FeatureCollection([ee.Feature(roi)]),
                                   reducer=ee.Reducer.mean(), scale=30, tileScale=4).getInfo()
    mvals = {k: v for k, v in (mstats["features"][0]["properties"]).items() if k[:2].isdigit()}
    meses = sorted(mvals)

    # --- MapBiomas: ano de conversão
    stack = ee.Image(MB_ASSET)
    natural = [3, 4, 5, 6, 11, 12, 32, 49, 50]
    antro = [9, 15, 19, 20, 21, 24, 30, 31, 35, 36, 39, 40, 41, 46, 47, 48, 62, 75]
    anos = list(range(1985, 2026))
    nat = ee.Image.cat([stack.select(f"classification_{a}").remap(natural, [1] * len(natural), 0)
                        .rename(f"n{a}") for a in anos])
    ant = ee.Image.cat([stack.select(f"classification_{a}").remap(antro, [1] * len(antro), 0)
                        .rename(f"a{a}") for a in anos])
    conv = []
    for i, a in enumerate(anos[1:], start=1):
        c = nat.select(f"n{anos[i - 1]}").multiply(ant.select(f"a{a}"))
        v = c.multiply(a).add(c.eq(0).multiply(9999))
        v = v.where(ee.Image.constant(a).gte(ano_lic), 9999)  # só conta a partir da licença
        conv.append(v.rename(f"c{a}"))
    conv_img = ee.Image.cat(conv).reduce(ee.Reducer.min()).rename("ano_conversao")
    conv_est = conv_img.reduceRegions(collection=ee.FeatureCollection([ee.Feature(roi)]),
                                      reducer=ee.Reducer.min(), scale=30, tileScale=4).getInfo()
    ano_conv = conv_est["features"][0]["properties"].get("min")

    largura = 700
    # --- imagens antes/depois
    # queda de NÍVEL entre anos (a queda mensal sazonal não é supressão)
    medias = {}
    for m in meses:
        medias.setdefault(m[:4], []).append(mvals[m])
    medias = {y: sum(v) / len(v) for y, v in medias.items() if v}
    anos_ord = sorted(medias)
    quedas_yoy = [(medias[anos_ord[i]] - medias[anos_ord[i - 1]], anos_ord[i])
                  for i in range(1, len(anos_ord))]
    if quedas_yoy:
        queda, ano_queda = min(quedas_yoy)
        anos_img = [int(ano_queda) - 1, int(ano_queda)]
        print("NDVI médio anual: " + " | ".join(f"{y}={medias[y]:.2f}" for y in anos_ord))
        print(f"queda de nível entre {int(ano_queda) - 1} e {ano_queda}: {queda:.3f} "
              f"| imagens: {anos_img}")
    else:
        ano_queda = None
        anos_img = [max(2018, ano_lic - 1), max(2019, ano_lic)]
    for a in anos_img:
        url = rgb(a, roi).getThumbURL({"dimensions": largura, "format": "png"})
        r = requests.get(url, timeout=120)
        destino = args.fig.with_name(args.fig.stem + f"_imagem_{a}.png")
        destino.write_bytes(r.content)
        print(f"imagem {a} -> {destino}")

    # --- figura
    fig, ax = plt.subplots(2, 1, figsize=(9, 7),
                           gridspec_kw={"height_ratios": [2, 1]})
    meses = sorted(mvals)
    ys = [mvals[m] for m in meses]
    xs = list(range(len(meses)))
    ax[0].plot(xs, ys, "-", color="#2c5a43", lw=1.6)
    ax[0].scatter(xs, ys, s=12, color="#2c5a43")
    if ano_queda:
        idx = [i for i, m in enumerate(meses) if m.startswith(str(ano_queda))]
        if idx:
            ax[0].axvspan(min(idx) - 0.5, max(idx) + 0.5, color="#c07a17", alpha=0.15)
            ax[0].text(min(idx), 0.98, f"queda de nível: {ano_queda}",
                       transform=ax[0].get_xaxis_transform(), va="top", fontsize=9, color="#8a5a12")
    if ano_conv and ano_conv != 9999:
        idx = [i for i, m in enumerate(meses) if m.startswith(str(int(ano_conv)))]
        if idx:
            ax[0].plot([max(idx)], [mvals[meses[max(idx)]]], "v", color="#a33a2a", ms=7)
            ax[0].text(max(idx), 0.86, f"MapBiomas: {int(ano_conv)}",
                       transform=ax[0].get_xaxis_transform(), ha="right", fontsize=9, color="#a33a2a")
    ax[0].set_ylabel("NDVI (Sentinel-2, mediana mensal)")
    ax[0].set_title(f"Processo {row.get('numero_processo')} — NDVI mensal (Sentinel-2) e anual (Landsat)")
    ax[0].grid(alpha=0.25)

    anos_l = sorted(lvals)
    val_l = [lvals[a] for a in anos_l]
    ax[1].plot(range(len(anos_l)), val_l, "o-", color="#8b6b3f")
    ax[1].set_xticks(range(len(anos_l)))
    ax[1].set_xticklabels([a.replace("ndvi_", "") for a in anos_l], rotation=45, ha="right")
    ax[1].set_ylabel("NDVI (Landsat C2 L2)")
    ax[1].grid(alpha=0.25)
    ax[1].set_xlabel("ano")
    ax[1].set_ylim(0, 1)

    step = max(1, len(meses) // 14)
    ax[0].set_xticks(xs[::step])
    ax[0].set_xticklabels([meses[i] for i in range(0, len(meses), step)], rotation=45, ha="right")
    fig.tight_layout()
    args.fig.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.fig, dpi=130)
    print(f"figura -> {args.fig.resolve()}")

    # --- série tabular
    serie = pd.DataFrame({"mes": meses, "ndvi_s2": ys})
    serie["ndvi_landsat"] = [lvals.get(f"ndvi_{m[:4]}") for m in meses]
    serie.to_csv(args.fig.with_suffix(".csv"), index=False, encoding="utf-8-sig")
    return 0


if __name__ == "__main__":
    sys.exit(main())
