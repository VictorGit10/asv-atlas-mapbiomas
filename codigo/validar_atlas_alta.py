"""
Validação do GetFeatureInfo contra o render do WMS em alta resolução.

O render grande (600 px para 2°) é grosseiro demais: um fragmento estreito
"borra" nos pixels vizinhos e o ponto parece estar sobre dado. Aqui renderizamos
uma janela pequena (alto zoom) ao redor de cada ponto e comparamos.
"""

from __future__ import annotations

import argparse
import io
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from PIL import Image

WMS = "https://atlas.meioambiente.go.gov.br/wms"
S = requests.Session()
S.headers.update({"User-Agent": "pesquisa-academica/1.0 (aluna CIAMB)"})


def janela(lat: float, lon: float, lado_m: float = 200.0, px: int = 128):
    """Render local (alto zoom). Devolve (tem_dado_no_centro, vizinho_mais_proximo)."""
    eps = lado_m / 2 / 111_320
    p = {"service": "WMS", "version": "1.3.0", "request": "GetMap",
         "layers": "atlas_remanescentes", "styles": "", "crs": "EPSG:4326",
         "bbox": f"{lat - eps},{lon - eps},{lat + eps},{lon + eps}",
         "width": px, "height": px, "format": "image/png"}
    try:
        r = S.get(WMS, params=p, timeout=90)
        if r.status_code != 200:
            return None, None
        a = np.asarray(Image.open(io.BytesIO(r.content)).convert("RGB"))
    except Exception:
        return None, None
    branco = np.all(a > 245, axis=2)
    c = px // 2
    return (not branco[c, c]), float((~branco).mean())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pontos", type=Path, default=Path("../dados/atlas_pontos.csv"))
    ap.add_argument("--amostra", type=int, default=60)
    ap.add_argument("--saida", type=Path, default=Path("../dados/atlas_validacao.csv"))
    args = ap.parse_args()

    df = pd.read_csv(args.pontos)
    rng = np.random.default_rng(3)
    sel = df.iloc[rng.choice(len(df), size=min(args.amostra, len(df)), replace=False)]

    def trabalho(row):
        centro, frac = janela(row.lat, row.lon)
        return {"id": row.id, "lat": row.lat, "lon": row.lon,
                "gfi_classificou": row.gfi_classificou,
                "render_centro_tem_dado": centro, "frac_dado_na_janela": frac}

    with ThreadPoolExecutor(max_workers=10) as ex:
        res = list(ex.map(lambda r: trabalho(r), [row for _, row in sel.iterrows()]))

    v = pd.DataFrame(res)
    ok = v.dropna(subset=["render_centro_tem_dado"])
    concord = (ok.gfi_classificou == ok.render_centro_tem_dado).mean()
    print(f"amostra validada: {len(ok)} pontos")
    print(pd.crosstab(ok.gfi_classificou, ok.render_centro_tem_dado).to_string())
    print(f"\nconcordância GFI x render (janela 200 m): {concord * 100:.1f}%")
    fp = ((~ok.gfi_classificou) & ok.render_centro_tem_dado).sum()
    fn = (ok.gfi_classificou & (~ok.render_centro_tem_dado)).sum()
    print(f"GFI perdeu fragmento: {fp} | GFI inventou: {fn}")
    v.to_csv(args.saida, index=False, encoding="utf-8-sig")


if __name__ == "__main__":
    main()
