"""
Resumo do resultado_asv.csv: taxa de supressão por formação e por ano.
Rodar depois de analisa_asv.py.
"""

from pathlib import Path

import pandas as pd

CSV = Path(__file__).parent.parent / "dados" / "resultado_asv.csv"

df = pd.read_csv(CSV)

print(f"polígonos analisados: {len(df)}")
print(f"área autorizada: {df.area_autorizada_ha.sum():,.1f} ha")
print(f"vegetação nativa antes da licença: {df.nat_ha_antes_da_licenca.sum():,.1f} ha")
print(f"suprimida depois da licença: {df.area_conv_pos_licenca_ha.sum():,.1f} ha")
print(f"taxa global de supressão: {100 * df.area_conv_pos_licenca_ha.sum() / df.nat_ha_antes_da_licenca.sum():.1f}%")

print("\n== por formação dominante ==")
g = df.groupby("classe_dominante").agg(
    n=("id", "count"),
    nativa_ha=("nat_ha_antes_da_licenca", "sum"),
    suprimida_ha=("area_conv_pos_licenca_ha", "sum"),
    delta_ndvi=("delta_ndvi", "median"),
)
g["taxa_%"] = (100 * g.suprimida_ha / g.nativa_ha).round(1)
print(g.sort_values("nativa_ha", ascending=False).round(2).to_string())

print("\n== por ano de licença ==")
a = df.groupby("ano_licenca").agg(
    n=("id", "count"),
    area_ha=("area_autorizada_ha", "sum"),
    nativa_ha=("nat_ha_antes_da_licenca", "sum"),
    suprimida_ha=("area_conv_pos_licenca_ha", "sum"),
)
a["taxa_%"] = (100 * a.suprimida_ha / a.nativa_ha).round(1)
print(a.round(1).to_string())
