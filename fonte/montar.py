import base64, html as htmlmod, pathlib
raiz = pathlib.Path(__file__).resolve().parent.parent
html = (raiz / "fonte" / "apresentacao.src.html").read_text(encoding="utf-8")
for f in ["serie_607_2019.png", "serie_607_2020.png", "serie_607.png", "atlas_anapolis_483.png", "atlas_anapolis_481.png"]:
    b = base64.b64encode((raiz / f).read_bytes()).decode()
    html = html.replace('src="../%s"' % f, 'src="data:image/png;base64,%s"' % b)
html = html.replace("{{FICHA}}", htmlmod.escape((raiz / "ficha_asv_code_editor.js").read_text(encoding="utf-8")))
assert "../serie" not in html and "../atlas" not in html and "{{" not in html
(raiz / "apresentacao.html").write_text(html, encoding="utf-8")
print("ok", len(html) // 1024, "KB")
