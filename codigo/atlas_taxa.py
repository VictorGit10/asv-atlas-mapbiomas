"""
Taxa de avanço do licenciamento sobre os remanescentes do Atlas (itens 2 e 4).

Renderiza a área de interesse em alta resolução (mosaico de tiles), classifica
cada pixel pela cor -> classe do Atlas, rasteriza TODAS as ASVs e cruza:

  área do Atlas por classe            (denominador)
  área autorizada sobre o Atlas/classe (numerador)
  taxa de avanço por classe           (item 2)
  proporcionalidade por formação      (item 4)

Uso:
    python atlas_taxa.py --geojson ../dados/asv_meiaponte.geojson --tiles 4 --px 500
"""

from __future__ import annotations

import argparse
import io
import re
import time
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
import requests
from PIL import Image
from rasterio.features import rasterize

WMS = "https://atlas.meioambiente.go.gov.br/wms"
S = requests.Session()
S.headers.update({"User-Agent": "pesquisa-academica/1.0 (aluna CIAMB)"})


def render(bbox, size, tentativas=6):
    minx, miny, maxx, maxy = bbox
    p = {"service": "WMS", "version": "1.3.0", "request": "GetMap",
         "layers": "atlas_remanescentes", "styles": "", "crs": "EPSG:4326",
         "bbox": f"{miny},{minx},{maxy},{maxx}", "width": size, "height": size,
         "format": "image/png"}
    for _ in range(tentativas):
        try:
            r = S.get(WMS, params=p, timeout=300)
        except requests.RequestException:
            time.sleep(5)
            continue
        if r.status_code == 200:
            return np.asarray(Image.open(io.BytesIO(r.content)).convert("RGB"))
        time.sleep(8)
    raise RuntimeError(f"GetMap falhou: {r.status_code} em {bbox}")


def gfi_classe(lat, lon, lado_m=250.0, px=64):
    eps = lado_m / 2 / 111_320
    p = {"service": "WMS", "version": "1.3.0", "request": "GetFeatureInfo",
         "layers": "atlas_remanescentes", "query_layers": "atlas_remanescentes",
         "styles": "", "crs": "EPSG:4326",
         "bbox": f"{lat - eps},{lon - eps},{lat + eps},{lon + eps}",
         "width": px, "height": px, "i": px // 2, "j": px // 2,
         "info_format": "text/plain", "feature_count": "1", "format": "image/png"}
    try:
        r = S.get(WMS, params=p, timeout=120)
    except requests.RequestException:
        return None
    m = re.search(r"CLASSES\s*=\s*([A-ZÀ-Ú0-9 \-_/]+?)\s+CD_MUN", r.text)
    return m.group(1).strip() if m else None


def erode(mask, n=4):
    for _ in range(n):
        m = mask.copy()
        m[1:, :] &= mask[:-1, :]
        m[:-1, :] &= mask[1:, :]
        m[:, 1:] &= mask[:, :-1]
        m[:, :-1] &= mask[:, 1:]
        mask = m
    return mask


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--geojson", type=Path, required=True)
    ap.add_argument("--tiles", type=int, default=4, help="grade de tiles (n x n)")
    ap.add_argument("--px", type=int, default=500, help="pixels por tile")
    ap.add_argument("--grupos", type=int, default=8)
    ap.add_argument("--pontos", type=Path, default=Path("../dados/atlas_pontos.csv"))
    ap.add_argument("--saida", type=Path, default=Path("../dados"))
    args = ap.parse_args()

    g = gpd.read_file(args.geojson)
    g["area_ha"] = g.to_crs(31982).area / 10000
    g = g.to_crs(4326)
    bounds = g.total_bounds
    print(f"ASVs: {len(g)} | área total: {g.area_ha.sum():,.0f} ha | bbox {np.round(bounds,3).tolist()}")

    # ------------------------------------------------ mosaico do Atlas
    n = args.tiles
    dx = (bounds[2] - bounds[0]) / n
    dy = (bounds[3] - bounds[1]) / n
    tiles = []
    for iy in range(n):
        for ix in range(n):
            b = (bounds[0] + ix * dx, bounds[1] + iy * dy,
                 bounds[0] + (ix + 1) * dx, bounds[1] + (iy + 1) * dy)
            print(f"  tile {iy}-{ix} ...", flush=True)
            tiles.append(render(b, args.px))
    H = n * args.px
    W = n * args.px
    img = np.zeros((H, W, 3), dtype=np.uint8)
    for k, t in enumerate(tiles):
        iy, ix = divmod(k, n)
        img[iy * args.px:(iy + 1) * args.px, ix * args.px:(ix + 1) * args.px] = t
    px_ha = ((bounds[2] - bounds[0]) * 111_320) * ((bounds[3] - bounds[1]) * 111_320) \
        * np.cos(np.radians((bounds[1] + bounds[3]) / 2)) / 1e4 / (H * W)
    print(f"mosaico {W}x{H} | 1 pixel = {px_ha:.2f} ha")

    # ------------------------------------------------ cores -> classes
    não_branco = ~np.all(img > 245, axis=2)
    px_all = img[não_branco].astype(float)
    rng = np.random.default_rng(5)
    amostra = px_all if len(px_all) <= 30000 else px_all[
        rng.choice(len(px_all), 30000, replace=False)]
    k = min(args.grupos, len(np.unique(amostra, axis=0)))
    cent = amostra[rng.choice(len(amostra), k, replace=False)]
    for _ in range(30):
        d = ((amostra[:, None, :] - cent[None, :, :]) ** 2).sum(axis=2)
        lab = d.argmin(axis=1)
        for c in range(k):
            m = lab == c
            if m.any():
                cent[c] = amostra[m].mean(axis=0)

    # rotulos supervisionados: pontos ja consultados via GetFeatureInfo
    rot = pd.read_csv(args.pontos) if args.pontos.exists() else None
    lab_cluster = {}
    if rot is not None and "classe_atlas" in rot:
        rot = rot.dropna(subset=["classe_atlas"])
        for _, r in rot.iterrows():
            x = int((r.lon - bounds[0]) / (bounds[2] - bounds[0]) * W)
            y = int((bounds[3] - r.lat) / (bounds[3] - bounds[1]) * H)
            if 0 <= x < W and 0 <= y < H:
                cor = tuple(int(v) for v in img[y, x])
                d = ((np.array(cor) - cent) ** 2).sum(axis=1)
                c = int(d.argmin())
                lab_cluster.setdefault(c, []).append(r.classe_atlas)
    print(f"clusters com rotulo direto: {len(lab_cluster)} "
          f"({sum(len(v) for v in lab_cluster.values())} pontos)")

    mapa = {}
    for c in range(k):
        votes = lab_cluster.get(c, [])
        classe = max(set(votes), key=votes.count) if votes else None
        if not classe:
            # fallback: sondar o WMS no interior do cluster
            lab_full = ((px_all[:, None, :] - cent[None, :, :]) ** 2).sum(axis=2).argmin(axis=1)
            mask = np.zeros((H, W), dtype=bool)
            mask[não_branco] = lab_full == c
            inter = erode(mask, 6)
            if not inter.any():
                inter = erode(mask, 2)
            if not inter.any():
                inter = mask
            js, is_ = np.where(inter)
            for _ in range(min(10, len(js))):
                idx = rng.choice(len(js))
                j, x = int(js[idx]), int(is_[idx])
                lon = bounds[0] + (x + 0.5) / W * (bounds[2] - bounds[0])
                lat = bounds[3] - (j + 0.5) / H * (bounds[3] - bounds[1])
                classe = gfi_classe(lat, lon, lado_m=600.0, px=64)
                if classe:
                    break
        mapa[c] = classe
        print(f"  cluster {c} rgb={np.round(cent[c]).astype(int).tolist()} "
              f"({len(votes)} rótulos) -> {classe}")

    # ------------------------------------------------ classificação final
    lab_all = ((px_all[:, None, :] - cent[None, :, :]) ** 2).sum(axis=2).argmin(axis=1)
    lab_img = np.full((H, W), -1, dtype=int)
    lab_img[não_branco] = lab_all
    classe_img = np.full((H, W), "SEM MAPEAMENTO", dtype=object)
    for c, v in mapa.items():
        classe_img[lab_img == c] = v if v else "SEM CLASSE"

    # ------------------------------------------------ ASV rasterizada
    mascara_asv = rasterize([(geom, 1) for geom in g.geometry], out_shape=(H, W),
                            transform=rasterio.transform.from_bounds(*bounds, W, H), fill=0,
                            dtype="uint8") > 0
    print(f"ASV rasterizada: {mascara_asv.sum() * px_ha:,.0f} ha (de {g.area_ha.sum():,.0f} ha)")

    linhas = []
    for classe in sorted(set(classe_img.ravel().tolist())):
        m = classe_img == classe
        if not m.any():
            continue
        area_total = m.sum() * px_ha
        area_asv = (m & mascara_asv).sum() * px_ha
        linhas.append({"classe_atlas": classe, "ha_atlas": round(area_total, 1),
                       "ha_autorizado": round(area_asv, 1),
                       "taxa_avanco_%": round(100 * area_asv / area_total, 2) if area_total else 0,
                       "formacao": str(classe).split()[0]})
    tab = pd.DataFrame(linhas).sort_values("ha_atlas", ascending=False)
    tab.to_csv(args.saida / "atlas_taxa_avanco.csv", index=False, encoding="utf-8-sig")
    print("\n== item 2: área e taxa de avanço do licenciamento por classe do Atlas ==")
    print(tab.to_string(index=False))

    print("\n== item 4: proporcionalidade por formação vegetal ==")
    fam = tab.groupby("formacao").agg(ha_atlas=("ha_atlas", "sum"),
                                      ha_autorizado=("ha_autorizado", "sum"))
    fam["taxa_%"] = (100 * fam.ha_autorizado / fam.ha_atlas).round(2)
    fam["prop_sobre_licenciado_%"] = (100 * fam.ha_autorizado / fam.ha_autorizado.sum()).round(1)
    print(fam.round(1).to_string())
    fam.to_csv(args.saida / "atlas_proporcionalidade_formacao.csv", encoding="utf-8-sig")


if __name__ == "__main__":
    main()
