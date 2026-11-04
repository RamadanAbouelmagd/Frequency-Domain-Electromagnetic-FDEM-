#!/usr/bin/env python3
"""Fill driver_app.template.html with one or more route JSON files.
   build_app.py OUT.html ROUTE1.json [ROUTE2.json ...] [--standalone]
--standalone wraps the page in a full HTML document (for GitHub Pages / any web host).
Without it the file is the fragment the Artifact tool expects."""
import json, sys, pathlib

here = pathlib.Path(__file__).parent
args = [a for a in sys.argv[1:] if not a.startswith("--")]
standalone = "--standalone" in sys.argv
out = args[0]
routes = []
for p in args[1:]:
    d = json.load(open(p))
    routes.extend(d if isinstance(d, list) else [d])
BG = {}
for r in routes:
    if "bg" in r:
        BG[r["bgKey"]] = r.pop("bg")
html = (here / "driver_app.template.html").read_text()
html = html.replace("/*ROUTES_JSON*/", json.dumps(routes, separators=(",", ":")).replace("</", "<\\/"))
html = html.replace("/*BG_JSON*/", json.dumps(BG, separators=(",", ":")).replace("</", "<\\/"))
if standalone:
    html = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
            '<meta name="apple-mobile-web-app-capable" content="yes">'
            '<style>:root{color-scheme:light dark}body{margin:0}</style></head><body>' + html + '</body></html>')
pathlib.Path(out).write_text(html)
print(f"{out}: {len(html) / 1024:.0f} KB, {len(routes)} route(s)")
