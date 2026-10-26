"""
Cruzamento Atlas x ASV por RASTER (e não por ponto).

O GetFeatureInfo erra ~1 em cada 7 fragmentos (efeito de borda/resolução).
Aqui a imagem do WMS é a fonte de verdade: renderiza-se a área de interesse em
alta resolução e conta-se pixel por pixel. As cores são traduzidas em classes
do Atlas com um punhado de sondas GetFeatureInfo (uma por cor dominante).

Saídas:
  atlas_cor_classe.csv   : mapa cor -> classe do Atlas
  atlas_area_por_asv.csv  : área por classe do Atlas em cada ASV
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


def render(bbox, size, tentativas=5):
    """Render WMS do bbox (minx,miny,maxx,maxy) em size x size."""
    minx, miny, maxx, maxy = bbox
    p = {"service": "WMS", "version": "1.3.0", "request": "GetMap",
         "layers": "atlas_remanescentes", "styles": "", "crs": "EPSG:4326",
         "bbox": f"{miny},{minx},{maxy},{maxx}", "width": size, "height": size,
         "format": "image/png"}
    for _ in range(tentativas):
        r = S.get(WMS, params=p, timeout=240)
        if r.status_code == 200:
            return np.asarray(Image.open(io.BytesIO(r.content)).convert("RGB"))
        time.sleep(6)
    raise RuntimeError(f"GetMap falhou: {r.status_code}")


def gfi_classe(lat, lon, lado_m=600.0, px=64):
    """Classe do Atlas no ponto (janela ampla, para pegar o fragmento vizinho)."""
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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--geojson", type=Path, required=True)
    ap.add_argument("--ids", type=Path, default=None)
    ap.add_argument("--px", type=int, default=300, help="pixels do lado do render")
    ap.add_argument("--grupos", type=int, default=8, help="clusters de cor (k-means)")
    ap.add_argument("--saida", type=Path, default=Path("../dados"))
    args = ap.parse_args()

    g = gpd.read_file(args.geojson)
    g["area_ha"] = g.to_crs(31982).area / 10000
    g = g.to_crs(4326)
    if args.ids and args.ids.exists():
        ids = set(pd.read_csv(args.ids)["id"].astype(str))
        g = g[[str(i) in ids for i in g.index]].copy()
    g["id"] = [str(i) for i in g.index]
    print(f"ASVs: {len(g)}")

    # 1) render global para descobrir as cores dominantes
    bounds = g.total_bounds
    img = render(bounds, args.px)
    H, W = img.shape[:2]
    cores, cont = np.unique(img.reshape(-1, 3), axis=0, return_counts=True)
    branco = np.all(cores > 245, axis=1)
    ordem = np.argsort(-cont)
    print(f"render {W}x{H} | {len(cores)} cores distintas")

    # 2) agrupar as cores (k-means) e sondar o Atlas em cada grupo.
    #    O estilo do WMS é antialiasado/gradiente: comparar cores exatas
    #    entre renders diferentes não funciona, agrupar funciona.
    não_branco = ~np.all(img > 245, axis=2)
    rng = np.random.default_rng(5)
    px_all = img[não_branco].astype(float)
    px = px_all
    if len(px) > 30000:
        px = px[np.random.default_rng(0).choice(len(px), 30000, replace=False)]
    k = min(args.grupos, len(np.unique(px, axis=0)))
    cent = px[np.random.default_rng(1).choice(len(px), k, replace=False)]
    for _ in range(30):
        d = ((px[:, None, :] - cent[None, :, :]) ** 2).sum(axis=2)
        lab = d.argmin(axis=1)
        for c in range(k):
            m = lab == c
            if m.any():
                cent[c] = px[m].mean(axis=0)
    lab_all = ((px_all[:, None, :] - cent[None, :, :]) ** 2).sum(axis=2).argmin(axis=1)
    lab_full = np.full((H, W), -1, dtype=int)
    lab_full[não_branco] = lab_all
    print(f"clusters de cor: {k}")

    def erode(mask, n=4):
        for _ in range(n):
            m = mask.copy()
            m[1:, :] &= mask[:-1, :]
            m[:-1, :] &= mask[1:, :]
            m[:, 1:] &= mask[:, :-1]
            m[:, :-1] &= mask[:, 1:]
            mask = m
        return mask

    mapa = {}
    for c in range(k):
        mask = (lab_full == c)
        inter = erode(mask, 6)
        if not inter.any():
            inter = erode(mask, 2)
        if not inter.any():
            inter = mask
        js, is_ = np.where(inter)
        if len(js) == 0:
            mapa[c] = None
            continue
        # tenta alguns pontos "interior" do cluster
        classe, j, x = None, None, None
        for k_ in range(min(6, len(js))):
            idx = rng.choice(len(js))
            j, x = int(js[idx]), int(is_[idx])
            lon = bounds[0] + (x + 0.5) / W * (bounds[2] - bounds[0])
            lat = bounds[3] - (j + 0.5) / H * (bounds[3] - bounds[1])
            classe = gfi_classe(lat, lon, lado_m=250.0, px=64)
            if classe:
                break
        n = int((lab_all == c).sum())
        mapa[c] = classe
        print(f"  cluster {c} rgb={np.round(cent[c]).astype(int).tolist()} "
              f"({n} px) -> {classe}")

    pd.DataFrame([{"cluster": c, "r": int(cent[c][0]), "g": int(cent[c][1]),
                    "b": int(cent[c][2]), "classe": v} for c, v in mapa.items()]).to_csv(
        args.saida / "atlas_cor_classe.csv", index=False, encoding="utf-8-sig")

    # 3) área por classe em cada ASV (render local por polígono)
    registros = []
    for _, row in g.iterrows():
        b = row.geometry.bounds
        tam = max(b[2] - b[0], b[3] - b[1]) * 1.15
        cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        bbox = (cx - tam / 2, cy - tam / 2, cx + tam / 2, cy + tam / 2)
        im = render(bbox, args.px)
        hh, ww = im.shape[:2]
        px_ha = (tam * 111_320) ** 2 / 10000 / (ww * hh)
        mascara = rasterize([(row.geometry, 1)], out_shape=(hh, ww),
                            transform=rasterio.transform.from_bounds(*bbox, ww, hh), fill=0)
        dentro = im[mascara == 1]
        if dentro.size == 0:
            continue
        por_classe = {}
        dd = ((dentro.astype(float)[:, None, :] - cent[None, :, :]) ** 2).sum(axis=2)
        lab_p = dd.argmin(axis=1)
        for c in range(k):
            n = int((lab_p == c).sum())
            if n == 0:
                continue
            classe = mapa.get(c)
            if not classe:
                continue
            por_classe[classe] = por_classe.get(classe, 0) + n * px_ha
        registros.append({"id": row.id, "processo": row.get("numero_processo"),
                          "area_ha": row.area_ha,
                          **{f"ha_{k}": round(v, 2) for k, v in por_classe.items()},
                          "ha_sem_mapeamento": round(
                              (mascara == 1).sum() * px_ha - sum(por_classe.values()), 2)})
        print(f"  ASV {row.id}: {len(por_classe)} classes | "
              f"{sum(por_classe.values()):.1f} ha de {row.area_ha:.1f} ha")

    df = pd.DataFrame(registros)
    df.to_csv(args.saida / "atlas_area_por_asv.csv", index=False, encoding="utf-8-sig")
    cols = [c for c in df.columns if c.startswith("ha_")]
    tot = df[cols].sum()
    print("\n== área por classe do Atlas nas ASV (30 amostras) ==")
    print(pd.DataFrame({"ha": tot.round(1), "%": (100 * tot / tot.sum()).round(1)}).to_string())


if __name__ == "__main__":
    main()
