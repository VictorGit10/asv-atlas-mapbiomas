"""
Cruzamento real: Atlas Goiás (WMS/GetFeatureInfo) x ASV (SIGA/GO) x MapBiomas (GEE).

O servidor do Atlas tem o WFS desativado, mas o WMS é vetorial e o
GetFeatureInfo devolve os atributos de cada fragmento (CLASSES, AREA_KM2,
município). A estratégia é amostrar pontos dentro de cada ASV e atribuir a
classe do Atlas, estimando área por classe por amostragem.

Saídas (em --saida):
  atlas_pontos.csv                  : ponto a ponto, com classe do Atlas
  atlas_por_asv.csv                 : área estimada por classe do Atlas, por ASV
  atlas_distribuicao_referencia.csv : distribuição de classes na área mapeada
  matriz_atlas_mapbiomas.csv        : confusão Atlas x MapBiomas
"""

from __future__ import annotations

import argparse
import io
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import requests
from PIL import Image
from shapely.geometry import Point

WMS = "https://atlas.meioambiente.go.gov.br/wms"
CAMADA = "atlas_remanescentes"
UA = {"User-Agent": "pesquisa-academica/1.0 (aluna CIAMB / orientacao)"}
SESSAO = requests.Session()
SESSAO.headers.update(UA)

# faixas do WMS em metros por pixel, para converter pixel -> coordenada
LADO_M = 90.0


def get_feature_info(lat: float, lon: float, px: int = 32) -> str | None:
    """Classe do Atlas no ponto (lat, lon); None se não há fragmento."""
    eps = LADO_M / 2 / 111_320
    params = {
        "service": "WMS", "version": "1.3.0", "request": "GetFeatureInfo",
        "layers": CAMADA, "query_layers": CAMADA, "styles": "", "crs": "EPSG:4326",
        "bbox": f"{lat - eps},{lon - eps},{lat + eps},{lon + eps}",
        "width": px, "height": px, "i": px // 2, "j": px // 2,
        "info_format": "text/plain", "feature_count": "1", "format": "image/png",
    }
    try:
        r = SESSAO.get(WMS, params=params, timeout=60)
        if r.status_code != 200:
            return None
    except requests.RequestException:
        return None
    m = re.search(r"CLASSES\s*=\s*([A-ZÀ-Ú0-9 \-_/]+?)\s+CD_MUN", r.text)
    return m.group(1).strip() if m else None


def paralelo(pontos, workers: int = 8):
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(lambda p: get_feature_info(p[0], p[1]), pontos))


def pontos_no_poligono(geom, n: int, seed: int):
    """n pontos aleatórios dentro do polígono (EPSG:4326), por rejeição."""
    rng = np.random.default_rng(seed)
    minx, miny, maxx, maxy = geom.bounds
    pts, tent = [], 0
    while len(pts) < n and tent < 300 * n:
        tent += 1
        p = Point(rng.uniform(minx, maxx), rng.uniform(miny, maxy))
        if geom.contains(p):
            pts.append((p.y, p.x))
    return pts


def fracao_mapeada(bounds, size: int = 1200) -> float:
    """Fração da área do bbox coberta pelo Atlas (pixels não brancos no WMS)."""
    minx, miny, maxx, maxy = bounds
    p = {"service": "WMS", "version": "1.3.0", "request": "GetMap", "layers": CAMADA,
         "styles": "", "crs": "EPSG:4326", "bbox": f"{miny},{minx},{maxy},{maxx}",
         "width": size, "height": size, "format": "image/png"}
    r = SESSAO.get(WMS, params=p, timeout=120)
    r.raise_for_status()
    im = Image.open(io.BytesIO(r.content)).convert("RGB")
    a = np.asarray(im)
    branco = np.all(a > 245, axis=2)
    return float((~branco).mean())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--geojson", type=Path, required=True)
    ap.add_argument("--ids", type=Path, default=None, help="CSV com a coluna id")
    ap.add_argument("--pontos", type=int, default=15)
    ap.add_argument("--referencia", type=int, default=200)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--saida", type=Path, default=Path("../dados"))
    args = ap.parse_args()

    gdf = gpd.read_file(args.geojson)
    gdf["area_ha"] = gdf.to_crs(31982).area / 10000
    gdf = gdf.to_crs(4326)
    if args.ids and args.ids.exists():
        ids = set(pd.read_csv(args.ids)["id"].astype(str))
        gdf = gdf[[str(i) in ids for i in gdf.index]].copy()
    gdf["id"] = [str(i) for i in gdf.index]
    bounds = gdf.total_bounds
    print(f"ASVs: {len(gdf)} | bbox: {np.round(bounds, 3).tolist()}")

    # ---------------------------------------------------------------- pontos
    t0, tarefas, rotulos = time.time(), [], []
    for _, row in gdf.iterrows():
        ps = pontos_no_poligono(row.geometry, args.pontos, seed=abs(hash(row.id)) % 10**6)
        tarefas.extend(ps)
        rotulos.extend([row.id] * len(ps))
    print(f"{len(tarefas)} pontos no WMS...", flush=True)
    classes = paralelo(tarefas, args.workers)
    print(f"  {time.time() - t0:.0f}s | com fragmento: {sum(c is not None for c in classes)}")

    df = pd.DataFrame({"id": rotulos, "lat": [p[0] for p in tarefas], "lon": [p[1] for p in tarefas],
                       "classe_atlas": classes})
    df.to_csv(args.saida / "atlas_pontos.csv", index=False, encoding="utf-8-sig")

    # ------------------------------------------------- área por classe / ASV
    area = gdf[["id", "numero_processo", "area_ha"]].copy()
    for classe in sorted(df["classe_atlas"].dropna().unique()):
        sub = df[df.classe_atlas == classe].groupby("id").size()
        area[f"ha_{classe}"] = area["id"].map(sub).fillna(0) / args.pontos * area["area_ha"]
    sem = df[df.classe_atlas.isna()].groupby("id").size()
    area["ha_sem_atlas"] = area["id"].map(sem).fillna(0) / args.pontos * area["area_ha"]
    area.to_csv(args.saida / "atlas_por_asv.csv", index=False, encoding="utf-8-sig")

    cols = [c for c in area.columns if c.startswith("ha_")]
    tot = area[cols].sum()
    print("\n== área (estimada) por classe do Atlas dentro das ASV ==")
    print(pd.DataFrame({"ha": tot.round(1), "%": (100 * tot / tot.sum()).round(1)}).to_string())

    # ------------------------------------------------- referência da bacia
    rng = np.random.default_rng(7)
    minx, miny, maxx, maxy = bounds
    ref = [(rng.uniform(miny, maxy), rng.uniform(minx, maxx)) for _ in range(args.referencia)]
    print(f"\n{len(ref)} pontos de referência...", flush=True)
    cref = paralelo(ref, args.workers)
    sref = pd.Series([c for c in cref if c is not None])
    dist = sref.value_counts()
    print(f"  com fragmento: {len(sref)}/{len(ref)}")
    print("\n== distribuição do Atlas entre os pontos mapeados ==")
    print(pd.DataFrame({"pts": dist, "%": (100 * dist / dist.sum()).round(1)}).to_string())
    dist.rename("n").to_csv(args.saida / "atlas_distribuicao_referencia.csv", encoding="utf-8-sig")

    # --------------------------------------- denominador: área do Atlas/classe
    frac = fracao_mapeada(bounds)
    area_bbox = (maxx - minx) * 111_320 * (maxy - miny) * 111_320 * np.cos(np.radians((miny + maxy) / 2)) / 10000
    area_mapeada = area_bbox * frac
    print(f"\nbbox: {area_bbox:,.0f} ha | coberto pelo Atlas: {frac * 100:.1f}% | área mapeada: {area_mapeada:,.0f} ha")
    den = pd.DataFrame({"classe": dist.index, "ha": (dist / dist.sum() * area_mapeada).round(1)})
    den["licenciado_ha"] = den.classe.map(tot).fillna(0).round(1)
    den["taxa_avanco_%"] = (100 * den.licenciado_ha / den.ha).round(2)
    print("\n== item 2: área e taxa de avanço do licenciamento por classe do Atlas ==")
    print(den.sort_values("ha", ascending=False).to_string(index=False))
    den.to_csv(args.saida / "atlas_avanco_por_classe.csv", index=False, encoding="utf-8-sig")

    # --------------------------------- item 4: proporcionalidade por formação
    fam = den.copy()
    fam["formacao"] = fam.classe.str.split().str[0]
    print("\n== item 4: proporcionalidade por formação vegetal ==")
    print(fam.groupby("formacao").agg(ha_atlas=("ha", "sum"), ha_licenciado=("licenciado_ha", "sum"),
                                      taxa=("taxa_avanco_%", "mean")).round(2).to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
