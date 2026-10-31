"""Diagnostic map: named boundary roads coloured, zone outlines, gap streets."""
import sys, csv, warnings
warnings.filterwarnings("ignore")
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
import osmnx as ox
import ua_routes as U
from shapely.geometry import LineString
from shapely.ops import unary_union

bbox = tuple(map(float, sys.argv[1].split(","))) if len(sys.argv) > 1 else None  # w,s,e,n
out = sys.argv[2] if len(sys.argv) > 2 else "diag_map.png"
G, Gu = U.prepare(ox.load_graphml(U.GRAPH_CACHE))
roads = U.Roads(G)
zones = {z: unary_union([U.ring_polygon(Gu, roads, r) for r in U.ZONES[z]["rings"]]) for z in U.ZONES}
city = U.city_boundary()
cmap = dict(zip(U.ROADS, plt.cm.tab10.colors))
w, s, e, n = bbox or unary_union(list(zones.values())).bounds
fig, ax = plt.subplots(figsize=(13, 13))
for u, v, d in Gu.edges(data=True):
    xs, ys = zip(*U.edge_coords(Gu, u, v, d))
    if max(xs) < w or min(xs) > e or max(ys) < s or min(ys) > n: continue
    labs = U.road_labels(d)
    if labs:
        lab = sorted(labs)[0]
        ax.plot(xs, ys, color=cmap[lab], lw=3.5, zorder=3)
    else:
        ax.plot(xs, ys, color="#aaaaaa", lw=0.7, zorder=1)
    if bbox and labs or (bbox and (e - w) < 0.03):
        nm = U.street_name(d)
        if len(xs) > 1 and LineString(zip(xs, ys)).length > 0.0015:
            m = LineString(zip(xs, ys)).interpolate(0.5, normalized=True)
            ax.annotate(nm.replace("West ", "W "), (m.x, m.y), fontsize=6, zorder=6, color="k")
zc = {"1": "red", "2": "orange", "3": "deepskyblue", "4": "green"}
for z, g in zones.items():
    for p in U.polys_of(g):
        ax.plot(*p.exterior.xy, color=zc[z], lw=1.5, ls="-", zorder=4)
if city is not None:
    for p in U.polys_of(city):
        ax.plot(*p.exterior.xy, color="k", lw=1.2, ls="--", zorder=5)
for lab, c in cmap.items():
    ax.plot([], [], color=c, lw=4, label=lab)
ax.legend(loc="upper right", fontsize=8)
ax.set_xlim(w, e); ax.set_ylim(s, n)
import math; ax.set_aspect(1 / math.cos(math.radians((s + n) / 2)))
fig.tight_layout(); fig.savefig(out, dpi=120)
