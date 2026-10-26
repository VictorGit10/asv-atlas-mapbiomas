"""
Baixa as Autorizações de Supressão de Vegetação (ASV) do SIGA/GO (SEMAD).

Fonte: WFS público do SIGA - https://siga.meioambiente.go.gov.br/geoserver/wfs
Camada: geonode:vw_asv_ipe_sga_publico (2.612 polígonos em out/2026)

Campos úteis: numero_processo, dataemissaolicenca, dataenviosinaflor,
              municipioibge, feicao, descricaoatividade, tipolicenca.

Uso:
    python baixa_asv.py --saida ../dados/asv_goias.geojson
    python baixa_asv.py --bbox -50.6,-18.3,-48.4,-16.0 --saida ../dados/asv_meiaponte.geojson
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import requests

WFS = "https://siga.meioambiente.go.gov.br/geoserver/wfs"
CAMADA = "geonode:vw_asv_ipe_sga_publico"


def baixa(bbox: str | None, cql: str | None, pagina: int, pausa: float) -> list[dict]:
    feicoes: list[dict] = []
    inicio = 0
    while True:
        params = {
            "service": "WFS",
            "version": "1.1.0",
            "request": "GetFeature",
            "typeName": CAMADA,
            "outputFormat": "application/json",
            "srsName": "EPSG:4326",
            "maxFeatures": pagina,
            "startIndex": inicio,
        }
        if bbox:
            params["bbox"] = bbox if bbox.count(",") == 4 else f"{bbox},EPSG:4326"
        if cql:
            params["CQL_FILTER"] = cql

        r = requests.get(WFS, params=params, timeout=180)
        r.raise_for_status()
        lote = r.json().get("features", [])
        feicoes.extend(lote)
        print(f"  {len(feicoes)} feições...", flush=True)
        if len(lote) < pagina:
            break
        inicio += pagina
        time.sleep(pausa)
    return feicoes


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--saida", type=Path, required=True)
    ap.add_argument("--bbox", default=None, help="minx,miny,maxx,maxy (WGS84)")
    ap.add_argument("--cql", default=None,
                    help="ex.: dataemissaolicenca BETWEEN 2019-01-01T00:00:00Z AND 2023-12-31T23:59:59Z")
    ap.add_argument("--pagina", type=int, default=500)
    ap.add_argument("--pausa", type=float, default=1.0, help="segundos entre páginas")
    args = ap.parse_args()

    feicoes = baixa(args.bbox, args.cql, args.pagina, args.pausa)
    args.saida.parent.mkdir(parents=True, exist_ok=True)
    args.saida.write_text(
        json.dumps({"type": "FeatureCollection", "features": feicoes}, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"{len(feicoes)} feições -> {args.saida.resolve()}")


if __name__ == "__main__":
    main()
