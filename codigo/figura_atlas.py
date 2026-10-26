"""
Figura de uma autorização (ASV) sobre os remanescentes do Atlas e a imagem Sentinel-2.

Cada painel é o mosaico da seca (jun-set) do Sentinel-2 em cor natural com o contorno da
autorização; com "+atlas" (ex.: 2025+atlas), também os polígonos do Atlas coloridos por classe.

Uso:
    python figura_atlas.py --atlas Anápolis.gpkg --geojson ../dados/asv_meiaponte.geojson --id 483 --paineis 2025 2025+atlas 2026
"""
import argparse
import io
import pathlib

import ee
import geopandas as gpd
import matplotlib.pyplot as plt
import requests
from matplotlib.patches import Patch
from PIL import Image

CORES = {
    "FLORESTAL SECA": "#1b7837", "FLORESTAL ÚMIDA": "#35978f",
    "SAVANA SECA": "#d9b300", "SAVANA ÚMIDA": "#80cdc1",
    "CAMPO SECO": "#e08214", "CAMPO ÚMIDO": "#92c5de",
}
UTM = 31982


def imagem_seca(ano, regiao, largura):
    col = (ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
           .filterBounds(regiao)
           .filterDate(f"{ano}-06-01", f"{ano}-10-01")
           .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 20)))
    vis = col.median().visualize(bands=["B4", "B3", "B2"], min=0, max=3000)
    url = vis.getThumbURL({"region": regiao, "dimensions": largura, "format": "png", "crs": "EPSG:4326"})
    return Image.open(io.BytesIO(requests.get(url, timeout=120).content)).convert("RGB"), col.size().getInfo()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--atlas", required=True)
    ap.add_argument("--geojson", required=True)
    ap.add_argument("--id", type=int, required=True)
    ap.add_argument("--paineis", nargs="+", required=True, help="ano ou ano+atlas")
    ap.add_argument("--margem", type=float, default=400, help="margem em metros em volta da autorização")
    ap.add_argument("--fig", type=pathlib.Path, required=True)
    ap.add_argument("--projeto", default="extreme-height-447417-a9")
    args = ap.parse_args()
    ee.Initialize(project=args.projeto)

    asv = gpd.read_file(args.geojson).loc[[args.id]].to_crs(UTM)
    minx, miny, maxx, maxy = asv.total_bounds
    lado = max(maxx - minx, maxy - miny) / 2 + args.margem
    cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
    quadro = gpd.GeoSeries.from_xy([cx - lado, cx + lado], [cy - lado, cy + lado], crs=UTM).to_crs(4326)
    x0, x1 = quadro.x.min(), quadro.x.max()
    y0, y1 = quadro.y.min(), quadro.y.max()
    regiao = ee.Geometry.Rectangle([x0, y0, x1, y1])

    atlas = gpd.read_file(args.atlas, bbox=(x0, y0, x1, y1)).to_crs(4326)
    atlas = atlas.clip((x0, y0, x1, y1))
    asv4326 = asv.to_crs(4326)

    n_p = len(args.paineis)
    fig, eixos = plt.subplots(1, n_p, figsize=(5.2 * n_p, 6.0))
    eixos = [eixos] if n_p == 1 else eixos
    imagens = {}
    for ax, painel in zip(eixos, args.paineis):
        ano, com_atlas = int(painel.split("+")[0]), painel.endswith("+atlas")
        if ano not in imagens:
            imagens[ano] = imagem_seca(ano, regiao, 900)
            print(f"{ano}: {imagens[ano][1]} imagens")
        ax.imshow(imagens[ano][0], extent=(x0, x1, y0, y1))
        if com_atlas:
            for classe, grupo in atlas.groupby("CLASSES"):
                grupo.plot(ax=ax, color=CORES.get(classe, "#999999"), alpha=0.45, edgecolor=CORES.get(classe, "#999999"), linewidth=0.8)
        asv4326.boundary.plot(ax=ax, color="#ff2bd6", linewidth=2.4)
        ax.set_xlim(x0, x1)
        ax.set_ylim(y0, y1)
        ax.set_xticks([])
        ax.set_yticks([])
        ax.set_title(f"Seca de {ano}" + (" + Atlas" if com_atlas else ""), fontsize=13, pad=8)
    classes = sorted(atlas.CLASSES.unique())
    itens = [Patch(facecolor="none", edgecolor="#ff2bd6", linewidth=2.4, label="Área autorizada")]
    itens += [Patch(facecolor=CORES.get(c, "#999999"), alpha=0.6, label=f"Atlas: {c.capitalize()}") for c in classes]
    fig.legend(handles=itens, loc="lower center", ncol=min(4, len(itens)), frameon=False, fontsize=10)
    fig.tight_layout(rect=(0, 0.07, 1, 1))
    fig.savefig(args.fig, dpi=110)
    print("figura ->", args.fig)


if __name__ == "__main__":
    main()
