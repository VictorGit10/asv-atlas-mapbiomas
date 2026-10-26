"""
Validação do cruzamento: confere, no render do WMS, se os pontos que o
GetFeatureInfo classed como "sem Atlas" realmente estão fora de fragmento
(pixel branco) e vice-versa. Também mede a fração do bbox coberta pelo Atlas.
"""

from __future__ import annotations

import argparse
import io
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from PIL import Image

WMS = "https://atlas.meioambiente.go.gov.br/wms"
SESSAO = requests.Session()
SESSAO.headers.update({"User-Agent": "pesquisa-academica/1.0 (aluna CIAMB)"})


def render(bounds, size: int) -> np.ndarray:
    minx, miny, maxx, maxy = bounds
    p = {"service": "WMS", "version": "1.3.0", "request": "GetMap",
         "layers": "atlas_remanescentes", "styles": "", "crs": "EPSG:4326",
         "bbox": f"{miny},{minx},{maxy},{maxx}", "width": size, "height": size,
         "format": "image/png"}
    for tentativa in range(4):
        r = SESSAO.get(WMS, params=p, timeout=180)
        if r.status_code == 200:
            return np.asarray(Image.open(io.BytesIO(r.content)).convert("RGB"))
        time.sleep(5)
    raise RuntimeError(f"GetMap falhou: {r.status_code}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pontos", type=Path, default=Path("../dados/atlas_pontos.csv"))
    ap.add_argument("--size", type=int, default=600)
    args = ap.parse_args()

    df = pd.read_csv(args.pontos)
    minx, miny, maxx, maxy = df.lon.min(), df.lat.min(), df.lon.max(), df.lat.max()
    bounds = (minx - 0.01, miny - 0.01, maxx + 0.01, maxy + 0.01)

    img = render(bounds, args.size)
    H, W = img.shape[:2]
    branco = np.all(img > 245, axis=2)
    print(f"render {W}x{H} | fração não-branca (Atlas): {(~branco).mean() * 100:.1f}%")

    x = ((df.lon - bounds[0]) / (bounds[2] - bounds[0]) * W).astype(int).clip(0, W - 1)
    y = ((bounds[3] - df.lat) / (bounds[3] - bounds[1]) * H).astype(int).clip(0, H - 1)
    com_dado_render = ~branco[y, x]

    df["render_tem_dado"] = com_dado_render
    df["gfi_classificou"] = df.classe_atlas.notna()
    conf = pd.crosstab(df.gfi_classificou, df.render_tem_dado)
    print("\n== GFI (classificou?) x render (pixel com dado?) ==")
    print(conf.to_string())

    ok = (df.gfi_classificou == df.render_tem_dado).mean()
    print(f"\nconcordância: {ok * 100:.1f}% dos {len(df)} pontos")
    fp = ((~df.gfi_classificou) & df.render_tem_dado).sum()
    fn = (df.gfi_classificou & (~df.render_tem_dado)).sum()
    print(f"GFI perdeu fragmento (render tem dado, GFI não): {fp}")
    print(f"GFI inventou fragmento (GFI tem dado, render não): {fn}")

    df.to_csv(args.pontos, index=False, encoding="utf-8-sig")
    np.save(Path(args.pontos).with_name("atlas_branco.npy"), branco)


if __name__ == "__main__":
    main()
