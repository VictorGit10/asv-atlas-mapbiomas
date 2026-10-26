"""
Cruzamento Atlas Goiás x ASV por POLÍGONOS (objetivos 1 e 2), um município por vez.

O Atlas pode ser baixado por município em https://siga.meioambiente.go.gov.br/atlas
(um .zip com um GeoPackage). Este script cruza esse arquivo com as autorizações
(ASV) do mesmo município e calcula a área por classe do Atlas, sem amostragem.

Saídas (em --saida):
  atlas_poligonos_<cod>_por_asv.csv    : área de cada classe do Atlas dentro de cada ASV
  atlas_poligonos_<cod>_por_classe.csv : objetivo 2, área do Atlas e área autorizada por classe

Uso:
    python atlas_poligonos.py --atlas Silvânia.gpkg --geojson ../dados/asv_meiaponte.geojson --municipio 5220603
"""
import argparse
import pathlib

import geopandas as gpd
import pandas as pd

# Albers do IBGE: projeção de área igual, para medir hectares
ALBERS = "+proj=aea +lat_0=-12 +lon_0=-54 +lat_1=-2 +lat_2=-22 +x_0=5000000 +y_0=10000000 +ellps=GRS80 +units=m +no_defs"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--atlas", required=True, help="GeoPackage do Atlas (um município)")
    ap.add_argument("--geojson", required=True, help="ASV baixadas com baixa_asv.py")
    ap.add_argument("--municipio", required=True, help="código IBGE do município")
    ap.add_argument("--saida", type=pathlib.Path, default=pathlib.Path("../dados"))
    args = ap.parse_args()

    atlas = gpd.read_file(args.atlas).to_crs(ALBERS)
    atlas["geometry"] = atlas.geometry.make_valid()
    # alguns polígonos do Atlas se sobrepõem; juntar por classe evita contar a mesma área duas vezes
    atlas = atlas[["CLASSES", "geometry"]].dissolve(by="CLASSES").reset_index()
    atlas["ha_atlas"] = atlas.area / 10000

    asv = gpd.read_file(args.geojson)
    asv = asv[asv.municipioibge == args.municipio].to_crs(ALBERS)
    asv["geometry"] = asv.geometry.make_valid()
    asv["area_ha"] = asv.area / 10000
    asv["ano_licenca"] = pd.to_datetime(asv.dataemissaolicenca, utc=True, errors="coerce").dt.year
    # a base publica a mesma geometria uma vez por atividade do processo
    asv["area_r"] = asv.area_ha.round(2)
    asv = asv.drop_duplicates(subset=["numero_processo", "area_r"]).copy()
    asv["id"] = asv.index

    # sobreposição entre ASVs do mesmo município contaria a mesma área duas vezes no total
    uniao_ha = asv.geometry.union_all().area / 10000

    cruz = gpd.overlay(asv[["id", "numero_processo", "ano_licenca", "area_ha", "geometry"]],
                       atlas[["CLASSES", "geometry"]], how="intersection", keep_geom_type=True)
    cruz["ha"] = cruz.area / 10000

    por_asv = cruz.pivot_table(index="id", columns="CLASSES", values="ha", aggfunc="sum", fill_value=0)
    por_asv.columns = [f"ha_{c}" for c in por_asv.columns]
    por_asv = asv.set_index("id")[["numero_processo", "ano_licenca", "area_ha"]].join(por_asv).fillna(0)
    cols = [c for c in por_asv.columns if c.startswith("ha_")]
    por_asv["ha_no_atlas"] = por_asv[cols].sum(axis=1)
    por_asv["pct_no_atlas"] = (100 * por_asv.ha_no_atlas / por_asv.area_ha).round(1)
    por_asv.round(2).to_csv(args.saida / f"atlas_poligonos_{args.municipio}_por_asv.csv", encoding="utf-8-sig")

    # objetivo 2: área autorizada sobre o remanescente de cada classe
    uniao_cruz = gpd.overlay(gpd.GeoDataFrame(geometry=[asv.geometry.union_all()], crs=ALBERS),
                             atlas[["CLASSES", "geometry"]], how="intersection", keep_geom_type=True)
    uniao_cruz["ha"] = uniao_cruz.area / 10000
    por_classe = pd.DataFrame({
        "ha_atlas": atlas.groupby("CLASSES").ha_atlas.sum(),
        "ha_autorizado_sobre_atlas": uniao_cruz.groupby("CLASSES").ha.sum(),
    }).fillna(0)
    por_classe["taxa_pct"] = (100 * por_classe.ha_autorizado_sobre_atlas / por_classe.ha_atlas).round(2)
    por_classe.round(2).to_csv(args.saida / f"atlas_poligonos_{args.municipio}_por_classe.csv", encoding="utf-8-sig")

    print(f"Atlas: {len(atlas)} polígonos, {atlas.ha_atlas.sum():,.0f} ha")
    print(f"ASV: {len(asv)} áreas, {asv.area_ha.sum():,.1f} ha somadas, {uniao_ha:,.1f} ha sem sobreposição")
    print(f"autorizado dentro do Atlas: {uniao_cruz.ha.sum():,.1f} ha ({100 * uniao_cruz.ha.sum() / uniao_ha:.1f}%)")
    print("\n== por classe (objetivo 2) ==")
    print(por_classe.round(2).to_string())
    print("\n== por ASV ==")
    print(por_asv[["numero_processo", "ano_licenca", "area_ha", "ha_no_atlas", "pct_no_atlas"]].round(1).to_string())


if __name__ == "__main__":
    main()
