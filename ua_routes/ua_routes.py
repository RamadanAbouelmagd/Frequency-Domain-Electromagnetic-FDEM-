#!/usr/bin/env python3
"""
Upper Arlington, OH - collection-zone route builder (Zones 1-4)
================================================================

  Zone 1  Monday     S of Canterbury & W of Tremont  +  Lane Ave -> 5th Ave,
                     Riverside Dr -> North Star Rd           (right-turn bias)
                     START Canterbury & Riverside   END 5th & North Star
  Zone 2  Tuesday    N of Canterbury, S of Fishinger, W of Tremont, E of Riverside
                     START Fishinger & Riverside    END Canterbury & Tremont
  Zone 3  Wednesday  N of Lane, E of Tremont/Reed, W of Kenny/North Star,
                     S of Henderson
                     START Henderson & Reed         END Lane & North Star
  Zone 4  Thursday   N of Fishinger, W of Reed, S of Henderson, E of Riverside
                     START Henderson & Riverside    END Fishinger & Reed

Pipeline per zone
-----------------
1. OpenStreetMap drive network for all of Upper Arlington (downloaded once,
   cached as ua_osm_graph.graphml).
2. Zone polygon traced ALONG the named boundary roads between their
   intersections (found by road name - no typed coordinates). Zone 1 is the
   union of its two boxes. Or draw your own polygon: --zone-kml.
3. Every drivable street segment inside the zone = required.
4. Chinese Postman, SINGLE PASS (each street driven once, one side-pass):
   MST connectors + min-weight matching -> one continuous route START -> END.
5. Turn smoothing: same streets, same miles, re-sequenced at every
   intersection to minimise U-turns and left turns (strong right-turn bias
   on Zone 1).
6. Outputs: <day>_route.kmz / .gpx, <day>_turn_sheet.csv, <day>_streets.csv,
   <day>_preview.png  (day = monday / tuesday / wednesday / thursday)
   and with --zone all: ua_zones_overview.kmz + ua_zone_overlaps.csv

Install:  pip install osmnx networkx simplekml shapely pyproj scipy
Run:      python ua_routes.py --zone all
          python ua_routes.py --zone 1
          python ua_routes.py --zone 3 --zone-kml my_zone3.kml
          python ua_routes.py --zone 2 --start 40.03,-83.09 --end 40.02,-83.07
"""

import argparse
import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)
import csv
import heapq
import math
import re
import sys
from collections import defaultdict

import networkx as nx
import simplekml
from pyproj import Transformer
from scipy.optimize import linear_sum_assignment
from shapely.geometry import LineString, MultiPolygon, Polygon
from shapely.ops import transform as shp_transform, unary_union

# ----------------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------------
# One generous box around all of Upper Arlington (lon/lat). Real clipping is
# done by the zone polygons; widen this if a road is reported missing.
BBOX = dict(west=-83.130, south=39.970, east=-83.020, north=40.090)
GRAPH_CACHE = "ua_osm_graph.graphml"

# Road name patterns (case-insensitive, matched on OSM name + ref)
ROADS = {
    "henderson":  r"(?<!old )\bhenderson\s+r(oa)?d\b",   # not Old Henderson / Henderson Heights
    "reed":       r"\breed\s+r(oa)?d\b",
    "fishinger":  r"\bfishinger\b",
    "riverside":  r"\briverside\s+dr|\bus\s*33\b|\b(oh|sr)\s*257\b",
    "canterbury": r"\bcanterbury\s+r(oa)?d\b",
    "tremont":    r"\btremont\s+r(oa)?d\b",
    "lane":       r"\blane\s+av",
    "north_star": r"\bnorth\s*star\s+(r|av)",          # Rd north of ~Tremont end, Ave south to 5th
    "fifth":      r"\b(5th|fifth)\s+av",
    "kenny":      r"\bkenny\s+r(oa)?d\b",
}

# rings = boundary roads in order around the zone (corners = consecutive pairs)
ZONES = {
    "1": dict(name="Monday Collection", day="monday",
              # A: Canterbury->Lane strip west of Tremont (Tremont never reaches 5th Ave;
              #    everything south of Lane is already in B)
              # B: south of Lane to 5th Ave, Riverside -> North Star
              rings=[["canterbury", "tremont", "lane", "riverside"],
                     ["lane", "north_star", "fifth", "riverside"]],
              start=("canterbury", "riverside"), end=("fifth", "north_star"),
              right_turn_bias=True),
    "2": dict(name="Tuesday Collection", day="tuesday",
              rings=[["fishinger", "tremont", "canterbury", "riverside"]],
              start=("fishinger", "riverside"), end=("canterbury", "tremont"),
              right_turn_bias=False),
    "3": dict(name="Wednesday Collection", day="wednesday",
              # Reed ends at Fishinger ~800 m west of Tremont; North Star ends at
              # Fishinger ~300 m west of Kenny -> both joins run along Fishinger
              rings=[["henderson", "kenny", "fishinger", "north_star", "lane", "tremont",
                      "fishinger", "reed"]],
              start=("henderson", "reed"), end=("lane", "north_star"),
              right_turn_bias=False),
    "4": dict(name="Thursday Collection", day="thursday",
              rings=[["henderson", "reed", "fishinger", "riverside"]],
              start=("henderson", "riverside"), end=("fishinger", "reed"),
              right_turn_bias=False),
}

INCLUDE_BOUNDARY_ROADS = True
CLIP_TO_CITY = True          # drop anything outside Upper Arlington city limits (OSM)
CITY_EDGE_M = 30             # keep boundary roads lying on the city line
# Unzoned pockets of the city: {zone: [(lat, lon), ...]} - a pocket containing one
# of these points is added to that zone. Filled in only after the user decides.
# Assigned per the city's official collection-day map (updated 3/08):
#   east of Kenny Rd between McCoy and Fishinger -> Wednesday
#   north of Henderson by US-33 (Tarrington / Bethel / Horizons) -> Thursday (only neighbour)
GAP_ASSIGN = {"3": [(40.03482, -83.04844)], "4": [(40.06325, -83.10108)]}
EDGE_BUFFER_M = 12
EXCLUDE_HIGHWAY = {"motorway", "motorway_link", "trunk_link", "service", "services"}
SERVICE_MPH = 8
DEADHEAD_MPH = 22
TURN_COST = dict(straight=0, right=1, left=3, uturn=8)
TURN_COST_RIGHT_BIAS = dict(straight=0, right=1, left=6, uturn=12)

UTM = "EPSG:32617"
to_utm = Transformer.from_crs("EPSG:4326", UTM, always_xy=True).transform
M_PER_MI = 1609.344
FT_PER_M = 3.28084


# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------
def as_list(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return []
    return v if isinstance(v, list) else [v]


def street_name(d):
    names = [str(n) for n in as_list(d.get("name"))]
    if names:
        return " / ".join(names)
    refs = [str(r) for r in as_list(d.get("ref"))]
    return refs[0] if refs else "Unnamed road"


def road_labels(d):
    text = " ".join(str(x) for x in as_list(d.get("name")) + as_list(d.get("ref"))).lower()
    return {lab for lab, pat in ROADS.items() if re.search(pat, text)}


def node_xy(G, n):
    return G.nodes[n]["x"], G.nodes[n]["y"]


def edge_coords(G, u, v, d):
    if d.get("geometry") is not None:
        c = list(d["geometry"].coords)
        ux, uy = node_xy(G, u); vx, vy = node_xy(G, v)
        if (c[0][0] - vx) ** 2 + (c[0][1] - vy) ** 2 < (c[0][0] - ux) ** 2 + (c[0][1] - uy) ** 2:
            c = c[::-1]
        return c
    return [node_xy(G, u), node_xy(G, v)]


def best_edge(G, u, v):
    ed = G.get_edge_data(u, v)
    if not ed:
        return None, None
    k = min(ed, key=lambda kk: ed[kk].get("length", 0))
    return k, ed[k]


def path_coords(G, path):
    out = []
    for u, v in zip(path[:-1], path[1:]):
        c = edge_coords(G, u, v, best_edge(G, u, v)[1])
        out.extend(c if not out else c[1:])
    return out


def path_length(G, path):
    return sum(best_edge(G, u, v)[1]["length"] for u, v in zip(path[:-1], path[1:]))


def nearest_node(G, lat, lon):
    return min(G.nodes, key=lambda n: (G.nodes[n]["x"] - lon) ** 2 + (G.nodes[n]["y"] - lat) ** 2)


# ----------------------------------------------------------------------------
# graph
# ----------------------------------------------------------------------------
def load_graph(graphml=None):
    import os
    import osmnx as ox
    path = graphml or (GRAPH_CACHE if os.path.exists(GRAPH_CACHE) else None)
    if path:
        print(f"Loading {path}")
        return ox.load_graphml(path)
    print("Downloading OSM drive network for Upper Arlington ...")
    G = ox.graph_from_bbox((BBOX["west"], BBOX["south"], BBOX["east"], BBOX["north"]),
                           network_type="drive", simplify=True, truncate_by_edge=True)
    ox.save_graphml(G, GRAPH_CACHE)
    return G


def prepare(G):
    scc = max(nx.strongly_connected_components(G), key=len)
    G = G.subgraph(scc).copy()
    Gu = nx.MultiGraph()
    Gu.graph.update(G.graph)
    Gu.add_nodes_from(G.nodes(data=True))
    seen = set()
    for u, v, k, d in G.edges(keys=True, data=True):
        sig = (min(u, v), max(u, v), str(d.get("osmid")), round(d.get("length", 0), 1))
        if sig not in seen:
            seen.add(sig)
            Gu.add_edge(u, v, **d)
    print(f"Network: {G.number_of_nodes()} nodes, {G.number_of_edges()} directed edges")
    return G, Gu


# ----------------------------------------------------------------------------
# zone geometry
# ----------------------------------------------------------------------------
class Roads:
    def __init__(self, G):
        self.G = G
        self.node_roads = defaultdict(set)
        self.road_nodes = defaultdict(set)
        self.warned = set()
        for u, v, d in G.edges(data=True):
            for lab in road_labels(d):
                self.node_roads[u].add(lab); self.node_roads[v].add(lab)
                self.road_nodes[lab].update((u, v))

    def check(self, labs):
        miss = [l for l in labs if not self.road_nodes[l]]
        if miss:
            sys.exit(f"ERROR: road(s) {miss} not found in the OSM download. "
                     "Widen BBOX or adjust the ROADS patterns.")

    def candidates(self, a, b):
        shared = [n for n in self.node_roads if {a, b} <= self.node_roads[n]]
        if shared:
            return shared, False
        best = None
        for na in self.road_nodes[a]:
            xa, ya = node_xy(self.G, na)
            for nb in self.road_nodes[b]:
                xb, yb = node_xy(self.G, nb)
                dd = (xa - xb) ** 2 + (ya - yb) ** 2
                if best is None or dd < best[0]:
                    best = (dd, na, nb)
        if (a, b) not in self.warned:
            self.warned.add((a, b))
            gap = math.hypot((node_xy(self.G, best[1])[0] - node_xy(self.G, best[2])[0])
                             * 111320 * math.cos(math.radians(node_xy(self.G, best[1])[1])),
                             (node_xy(self.G, best[1])[1] - node_xy(self.G, best[2])[1]) * 110574)
            print(f"  WARNING: {a} and {b} don't intersect in OSM - using nearest point "
                  f"(~{gap:.0f} m gap). Check this corner.")
        return [best[1]], True

    def corner(self, a, b, center):
        cands, _ = self.candidates(a, b)
        cx, cy = center
        return min(cands, key=lambda n: (node_xy(self.G, n)[0] - cx) ** 2 +
                                        (node_xy(self.G, n)[1] - cy) ** 2)


def ring_polygon(Gu, roads, ring):
    roads.check(ring)
    pairs = [(ring[i], ring[(i + 1) % len(ring)]) for i in range(len(ring))]
    cand = {p: roads.candidates(*p)[0] for p in pairs}
    pts = [node_xy(Gu, n) for c in cand.values() for n in c]
    center = (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
    corner = {p: roads.corner(*p, center) for p in pairs}
    coords = []
    for i, lab in enumerate(ring):
        a = corner[pairs[i - 1]]          # (prev road, this road)
        b = corner[pairs[i]]              # (this road, next road)
        sub = Gu.edge_subgraph([(u, v, k) for u, v, k, d in Gu.edges(keys=True, data=True)
                                if lab in road_labels(d)])
        try:
            p = nx.shortest_path(sub, a, b, weight="length")
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            print(f"  WARNING: '{lab}' not continuous between its corners in OSM - "
                  "using general shortest path for that side.")
            p = nx.shortest_path(Gu, a, b, weight="length")
        c = path_coords(Gu, p) if len(p) > 1 else [node_xy(Gu, a)]
        coords.extend(c if not coords else c[1:])
    poly = Polygon(coords)
    return poly if poly.is_valid else poly.buffer(0)


def polygon_from_kml(path):
    txt = open(path, encoding="utf-8").read()
    polys = []
    for block in re.findall(r"<Polygon.*?</Polygon>", txt, re.S):
        c = re.search(r"<outerBoundaryIs>.*?<coordinates>(.*?)</coordinates>", block, re.S)
        pts = [tuple(map(float, s.split(",")[:2])) for s in c.group(1).split()]
        polys.append(Polygon(pts).buffer(0))
    if not polys:
        sys.exit(f"ERROR: no <Polygon> found in {path}")
    return unary_union(polys)


def polys_of(geom):
    return list(geom.geoms) if isinstance(geom, MultiPolygon) else [geom]


# ----------------------------------------------------------------------------
# Chinese Postman
# ----------------------------------------------------------------------------
def connect_components(R, Gw, extra_nodes, directed):
    H = R.to_undirected() if directed else R
    comps = [set(c) for c in nx.connected_components(H)]
    for n in extra_nodes:
        if not any(n in c for c in comps):
            comps.append({n})
    if len(comps) <= 1:
        return []
    print(f"  connecting {len(comps)} separate pieces")
    K, links = nx.Graph(), {}
    for i, ci in enumerate(comps):
        dist, paths = nx.multi_source_dijkstra(Gw, ci, weight="length")
        for j in range(i + 1, len(comps)):
            tgt = min((n for n in comps[j] if n in dist), key=lambda n: dist[n], default=None)
            if tgt is None:
                continue
            p, cost = paths[tgt], dist[tgt]
            if directed:
                try:
                    back = nx.shortest_path(Gw, p[-1], p[0], weight="length")
                except nx.NetworkXNoPath:
                    continue
                cost += path_length(Gw, back)
                links[(i, j)] = (p, back)
            else:
                links[(i, j)] = (p,)
            K.add_edge(i, j, weight=cost)
    out = []
    for i, j in nx.minimum_spanning_edges(K, data=False):
        out.extend(links[(min(i, j), max(i, j))])
    return out


def solve_undirected(G, Gu, req, s, t):
    R = nx.MultiGraph()
    for u, v, k, d in req:
        R.add_edge(u, v, **d, service=True)
    R.add_nodes_from([s, t])
    for p in connect_components(R, Gu, [s, t], directed=False):
        for a, b in zip(p[:-1], p[1:]):
            R.add_edge(a, b, **best_edge(Gu, a, b)[1], service=False)

    odd = {n for n, deg in R.degree() if deg % 2}
    odd ^= {s, t}
    odd = sorted(odd)
    oddset = set(odd)
    print(f"  pairing {len(odd)} odd intersections")
    dist, paths = {}, {}
    for n in odd:
        dl, pl = nx.single_source_dijkstra(Gu, n, weight="length")
        dist[n] = {m: dl[m] for m in oddset if m in dl and m != n}
        paths[n] = pl

    def match(knn):
        M = nx.Graph()
        for n in odd:
            nb = sorted(dist[n].items(), key=lambda kv: kv[1])
            for m, dd in (nb[:knn] if knn else nb):
                M.add_edge(n, m, weight=dd)
        return nx.min_weight_matching(M)

    matching = match(25)
    if 2 * len(matching) != len(odd):
        matching = match(None)
    for a, b in matching:
        p = paths[a][b]
        for x, y in zip(p[:-1], p[1:]):
            R.add_edge(x, y, **best_edge(Gu, x, y)[1], service=False)

    if not nx.has_eulerian_path(R, source=s):
        sys.exit("ERROR: no Euler path after augmentation (unexpected).")
    steps = [(u, v, R[u][v][k]) for u, v, k in nx.eulerian_path(R, source=s, keys=True)]
    steps = orient_one_way(G, steps, s)
    return repair_one_way(G, steps)


def orient_one_way(G, steps, s):
    """Zero-extra-mile one-way fix. For every arc driven against a one-way,
    find a directed cycle through it made of route arcs on two-way streets
    and reverse the whole cycle: balance (and so the Euler path) and total
    miles are unchanged, the one-way leg becomes legal."""
    arcs = [[u, v, d] for u, v, d in steps]
    bad = [i for i, (u, v, d) in enumerate(arcs) if not G.has_edge(u, v)]
    if not bad:
        return steps
    fixed = 0
    for i in bad:
        u, v, _ = arcs[i]
        if G.has_edge(u, v):
            continue
        H = nx.DiGraph()
        for j, (x, y, d) in enumerate(arcs):
            if j != i and G.has_edge(y, x) and G.has_edge(x, y):       # reversible arc
                if not H.has_edge(x, y) or d["length"] < H[x][y]["length"]:
                    H.add_edge(x, y, length=d["length"], j=j)
        try:
            path = nx.shortest_path(H, v, u, weight="length")
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            continue
        for x, y in zip(path[:-1], path[1:]):
            j = H[x][y]["j"]
            arcs[j][0], arcs[j][1] = arcs[j][1], arcs[j][0]
        arcs[i][0], arcs[i][1] = v, u
        fixed += 1
    D = nx.MultiDiGraph()
    for x, y, d in arcs:
        D.add_edge(x, y, **d)
    left = sum(1 for x, y, d in arcs if not G.has_edge(x, y))
    print(f"  one-way orientation: {fixed} of {len(bad)} wrong-way arcs fixed at 0 extra miles"
          f" ({left} left)")
    return [(x, y, D[x][y][k]) for x, y, k in nx.eulerian_path(D, source=s, keys=True)]


def repair_one_way(G, steps):
    Gs = nx.DiGraph()
    for u, v, d in G.edges(data=True):
        if not Gs.has_edge(u, v) or d["length"] < Gs[u][v]["length"]:
            Gs.add_edge(u, v, length=d["length"])
    out, i = [], 0
    while i < len(steps):
        if steps[i][2]["service"]:
            out.append(steps[i]); i += 1; continue
        j = i
        while j < len(steps) and not steps[j][2]["service"]:
            j += 1
        run = steps[i:j]
        if any(not G.has_edge(u, v) for u, v, _ in run):
            a, b = run[0][0], run[-1][1]
            p = nx.shortest_path(Gs, a, b, weight="length") if a != b else [a]
            for x, y in zip(p[:-1], p[1:]):
                out.append((x, y, {**best_edge(G, x, y)[1], "service": False}))
        else:
            out.extend(run)
        i = j
    return out


# ----------------------------------------------------------------------------
# turn smoothing
# ----------------------------------------------------------------------------
def _heading(p, q):
    dx = (q[0] - p[0]) * math.cos(math.radians(p[1]))
    return math.degrees(math.atan2(q[1] - p[1], dx))


def step_headings(G, step):
    c = edge_coords(G, *step)
    first = next((q for q in c[1:] if q != c[0]), c[-1])
    last = next((q for q in reversed(c[:-1]) if q != c[-1]), c[0])
    return _heading(c[0], first), _heading(last, c[-1])


def classify(h_in, h_out):
    th = (h_out - h_in + 540) % 360 - 180          # + = left (CCW)
    if abs(th) >= 150:
        return "uturn"
    if abs(th) <= 30:
        return "straight"
    return "left" if th > 0 else "right"


def turn_stats(G, steps):
    hd = [step_headings(G, s) for s in steps]
    cnt = defaultdict(int)
    for a, b in zip(hd[:-1], hd[1:]):
        cnt[classify(a[1], b[0])] += 1
    return dict(cnt)


def optimize_turns(G, steps, s, t, costs):
    """Re-sequence a fixed set of arcs (same miles) to minimise turn cost.
    Per-node min-cost in->out pairing, then cheapest cycle merges."""
    m = len(steps)
    V = m                                             # virtual arc t -> s
    head = [v for u, v, d in steps] + [s]
    tail = [u for u, v, d in steps] + [t]
    hd = [step_headings(G, st) for st in steps] + [(None, None)]

    def tc(a, b):
        if a == V or b == V:
            return 0
        return costs[classify(hd[a][1], hd[b][0])]

    ins, outs = defaultdict(list), defaultdict(list)
    for i in range(m + 1):
        ins[head[i]].append(i); outs[tail[i]].append(i)
    succ = [None] * (m + 1)
    for v, I in ins.items():
        O = outs[v]
        if len(I) != len(O):
            print("  turn smoothing skipped (unbalanced node)")
            return steps
        r, c = linear_sum_assignment([[tc(a, b) for b in O] for a in I])
        for i, j in zip(r, c):
            succ[I[i]] = O[j]

    cyc, k = [-1] * (m + 1), 0
    for i in range(m + 1):
        if cyc[i] < 0:
            j = i
            while cyc[j] < 0:
                cyc[j] = k; j = succ[j]
            k += 1
    parent = list(range(k))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x

    def delta(a, b):
        return tc(a, succ[b]) + tc(b, succ[a]) - tc(a, succ[a]) - tc(b, succ[b])

    heap = []
    for v, I in ins.items():
        for x in range(len(I)):
            for y in range(x + 1, len(I)):
                a, b = I[x], I[y]
                if cyc[a] != cyc[b]:
                    heapq.heappush(heap, (delta(a, b), a, b))
    ncomp = k
    while heap and ncomp > 1:
        d, a, b = heapq.heappop(heap)
        if find(cyc[a]) == find(cyc[b]):
            continue
        nd = delta(a, b)
        if nd > d + 1e-9:
            heapq.heappush(heap, (nd, a, b)); continue
        succ[a], succ[b] = succ[b], succ[a]
        parent[find(cyc[a])] = find(cyc[b]); ncomp -= 1
    if ncomp > 1:
        print("  turn smoothing skipped (could not merge)")
        return steps
    out, i = [], succ[V]
    while i != V:
        out.append(steps[i]); i = succ[i]
    return out if len(out) == m else steps


# ----------------------------------------------------------------------------
# outputs
# ----------------------------------------------------------------------------
def group_steps(G, steps):
    hd = [step_headings(G, s) for s in steps]
    groups = []
    for i, (u, v, d) in enumerate(steps):
        name = street_name(d)
        wrong = d["service"] and not G.has_edge(u, v)
        c = edge_coords(G, u, v, d)
        g = groups[-1] if groups else None
        if g and g["street"] == name and g["service"] == d["service"] and g["wrong"] == wrong:
            g["coords"].extend(c[1:]); g["len"] += d["length"]
        else:
            turn = "start" if i == 0 else classify(hd[i - 1][1], hd[i][0])
            groups.append(dict(street=name, service=d["service"], wrong=wrong, turn=turn,
                               coords=list(c), len=d["length"], start=u))
    return groups


TURN_TXT = dict(start="Start on", straight="Continue on", right="Turn RIGHT onto",
                left="Turn LEFT onto", uturn="U-TURN onto")


def write_outputs(G, zid, zone, zone_geom, steps, s, t, stats_before, stats_after):
    pre = ZONES[zid]["day"]
    groups = group_steps(G, steps)
    full = []
    for g in groups:
        full.extend(g["coords"] if not full else g["coords"][1:])
    svc = sum(g["len"] for g in groups if g["service"])
    dh = sum(g["len"] for g in groups if not g["service"])
    hrs = svc / M_PER_MI / SERVICE_MPH + dh / M_PER_MI / DEADHEAD_MPH
    wrong = sum(1 for g in groups if g["wrong"])
    tstr = lambda st: ", ".join(f"{k} {st.get(k, 0)}" for k in ("right", "straight", "left", "uturn"))
    summary = (f"{zone['name']}: total {(svc + dh) / M_PER_MI:.2f} mi | collect "
               f"{svc / M_PER_MI:.2f} mi | repeat {dh / M_PER_MI:.2f} mi "
               f"({100 * dh / max(svc + dh, 1):.0f}%) | ~{hrs:.1f} h @ {SERVICE_MPH}/"
               f"{DEADHEAD_MPH} mph | turns: {tstr(stats_after)}")
    print("  " + summary)
    print(f"  turns before smoothing: {tstr(stats_before)}")
    if wrong:
        print(f"  NOTE: {wrong} one-way leg(s) against traffic (red in KMZ). "
              "Check those legs on the map; a short legal detour may be needed.")

    kml = simplekml.Kml(name=f"Upper Arlington - {zone['name']}")
    kml.document.description = summary
    fz = kml.newfolder(name="Zone boundary")
    for poly in polys_of(zone_geom):
        pg = fz.newpolygon(name=zone["name"], outerboundaryis=list(poly.exterior.coords))
        pg.style.polystyle.color = simplekml.Color.changealphaint(40, simplekml.Color.yellow)
        pg.style.linestyle.color = simplekml.Color.yellow
        pg.style.linestyle.width = 3
    ls = kml.newfolder(name="Route (continuous)").newlinestring(
        name="Full route START -> END", coords=full, description=summary)
    ls.style.linestyle.color = simplekml.Color.changealphaint(200, simplekml.Color.blue)
    ls.style.linestyle.width = 5
    ls.tessellate = 1

    fl = kml.newfolder(name="Legs (green collect / orange repeat / red wrong-way)")
    fl.visibility = 0
    for i, g in enumerate(groups, 1):
        l = fl.newlinestring(name=f"{i:03d} {g['street']}", coords=g["coords"])
        l.visibility = 0
        l.style.linestyle.color = (simplekml.Color.red if g["wrong"] else
                                   simplekml.Color.green if g["service"] else
                                   simplekml.Color.orange)
        l.style.linestyle.width = 4
    fa = kml.newfolder(name="Anchor intersections (numbered steps)")
    for i, g in enumerate(groups, 1):
        x, y = node_xy(G, g["start"])
        p = fa.newpoint(name=str(i), coords=[(x, y)])
        p.description = (f"{TURN_TXT[g['turn']]} {g['street']} - "
                         f"{'COLLECT' if g['service'] else 'drive (repeat)'} "
                         f"{g['len'] * FT_PER_M:.0f} ft")
        p.style.iconstyle.scale = 0.6
        p.style.labelstyle.scale = 0.6
        p.style.iconstyle.icon.href = "http://maps.google.com/mapfiles/kml/shapes/placemark_circle.png"
    for label, node, icon in [("START", s, "grn-stars"), ("END", t, "red-stars")]:
        x, y = node_xy(G, node)
        p = kml.newpoint(name=label, coords=[(x, y)])
        p.style.iconstyle.icon.href = f"http://maps.google.com/mapfiles/kml/paddle/{icon}.png"
        p.style.iconstyle.scale = 1.3
    kml.savekmz(f"{pre}_route.kmz")

    with open(f"{pre}_route.gpx", "w") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n<gpx version="1.1" '
                'creator="ua_routes" xmlns="http://www.topografix.com/GPX/1/1">\n'
                f'<trk><name>UA {zone["name"]}</name><trkseg>\n')
        for x, y in full:
            f.write(f'<trkpt lat="{y:.7f}" lon="{x:.7f}"/>\n')
        f.write("</trkseg></trk></gpx>\n")

    with open(f"{pre}_turn_sheet.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["step", "instruction", "street", "mode", "feet", "cum_miles",
                    "lat", "lon", "warning"])
        cum = 0.0
        for i, g in enumerate(groups, 1):
            cum += g["len"]
            x, y = node_xy(G, g["start"])
            w.writerow([i, TURN_TXT[g["turn"]], g["street"],
                        "COLLECT" if g["service"] else "repeat", round(g["len"] * FT_PER_M),
                        round(cum / M_PER_MI, 2), round(y, 6), round(x, 6),
                        "one-way street: check direction" if g["wrong"] else ""])
    print(f"  wrote {pre}_route.kmz, {pre}_route.gpx, {pre}_turn_sheet.csv, {pre}_streets.csv")
    return dict(zone=zid, name=zone["name"], total_mi=(svc + dh) / M_PER_MI,
                collect_mi=svc / M_PER_MI, repeat_mi=dh / M_PER_MI,
                repeat_pct=100 * dh / max(svc + dh, 1), hours=hrs, oneway_flags=wrong,
                oneway_streets="; ".join(sorted({g["street"] for g in groups if g["wrong"]})),
                **{f"turn_{k}": stats_after.get(k, 0) for k in ("right", "straight", "left", "uturn")},
                **{f"before_{k}": stats_before.get(k, 0) for k in ("right", "straight", "left", "uturn")})


def render_png(G, zid, zone, geom, steps, s, t, fname=None):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    minx, miny, maxx, maxy = geom.bounds
    pad = 0.004
    fig, ax = plt.subplots(figsize=(11, 11))
    for u, v, d in G.edges(data=True):
        c = edge_coords(G, u, v, d)
        xs, ys = zip(*c)
        if max(xs) < minx - pad or min(xs) > maxx + pad or max(ys) < miny - pad or min(ys) > maxy + pad:
            continue
        ax.plot(xs, ys, color="#bbbbbb", lw=0.6, zorder=1)
    for poly in polys_of(geom):
        xs, ys = poly.exterior.xy
        ax.fill(xs, ys, color="gold", alpha=0.12, zorder=0)
        ax.plot(xs, ys, color="goldenrod", lw=2.5, zorder=2)
    for u, v, d in steps:
        xs, ys = zip(*edge_coords(G, u, v, d))
        col = ("red" if d["service"] and not G.has_edge(u, v) else
               "tab:blue" if d["service"] else "darkorange")
        ax.plot(xs, ys, color=col, lw=1.6 if d["service"] else 2.4, zorder=3)
    for lab, n, col in (("START", s, "green"), ("END", t, "red")):
        x, y = node_xy(G, n)
        ax.scatter([x], [y], s=160, color=col, edgecolor="k", zorder=5)
        ax.annotate(lab, (x, y), xytext=(6, 6), textcoords="offset points", weight="bold")
    ax.set_xlim(minx - pad, maxx + pad); ax.set_ylim(miny - pad, maxy + pad)
    ax.set_aspect(1 / math.cos(math.radians((miny + maxy) / 2)))
    ax.set_title(f"{zone['name']}  (blue = collect, orange = repeat, red = against one-way)")
    fig.tight_layout()
    fig.savefig(fname or f"{ZONES[zid]['day']}_preview.png", dpi=130)
    plt.close(fig)


def city_boundary():
    """Upper Arlington city limit from OSM (Nominatim), cached as GeoJSON."""
    import json, os
    from shapely.geometry import shape, mapping
    fp = "ua_city_boundary.geojson"
    if os.path.exists(fp):
        return shape(json.load(open(fp)))
    try:
        import osmnx as ox
        g = ox.geocode_to_gdf("Upper Arlington, Ohio, USA").geometry.iloc[0]
        json.dump(mapping(g), open(fp, "w"))
        return g
    except Exception as e:                               # noqa: BLE001
        print(f"  (city boundary unavailable: {e.__class__.__name__}) - gap check skipped")
        return None


def check_gaps(Gu, results):
    city = city_boundary()
    if city is None:
        return
    cityb = shp_transform(to_utm, city)
    zone_u = shp_transform(to_utm, unary_union([g for g, *_ in results.values()])).buffer(EDGE_BUFFER_M)
    pockets = cityb.difference(zone_u.buffer(3)).buffer(-8).buffer(8)
    inv = Transformer.from_crs(UTM, "EPSG:4326", always_xy=True).transform
    with open("ua_zone_gap_pockets.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["lat", "lon", "hectares", "street_miles", "streets"])
        for p in sorted(polys_of(pockets), key=lambda q: -q.area):
            st = defaultdict(float)
            for u, v, k, d in Gu.edges(keys=True, data=True):
                if {str(h) for h in as_list(d.get("highway"))} & EXCLUDE_HIGHWAY:
                    continue
                m = shp_transform(to_utm, LineString(edge_coords(Gu, u, v, d))).interpolate(0.5, normalized=True)
                if p.contains(m):
                    st[street_name(d)] += d["length"]
            if not st:
                continue
            c = shp_transform(inv, p).representative_point()
            w.writerow([round(c.y, 5), round(c.x, 5), round(p.area / 1e4, 1),
                        round(sum(st.values()) / M_PER_MI, 2),
                        "; ".join(sorted(st, key=lambda n: -st[n]))])
    gaps, outside = defaultdict(float), defaultdict(float)
    for u, v, k, d in Gu.edges(keys=True, data=True):
        if {str(h) for h in as_list(d.get("highway"))} & EXCLUDE_HIGHWAY:
            continue
        ln = shp_transform(to_utm, LineString(edge_coords(Gu, u, v, d)))
        mid = ln.interpolate(0.5, normalized=True)
        if cityb.contains(mid) and not zone_u.contains(mid):
            gaps[street_name(d)] += d["length"]
        elif zone_u.contains(mid) and not cityb.buffer(30).contains(mid):
            outside[street_name(d)] += d["length"]
    for fn, data in (("ua_zone_gaps.csv", gaps), ("ua_zone_outside_city.csv", outside)):
        with open(fn, "w", newline="") as f:
            w = csv.writer(f); w.writerow(["street", "centerline_ft"])
            for n, L in sorted(data.items(), key=lambda kv: -kv[1]):
                w.writerow([n, round(L * FT_PER_M)])
    print(f"  gap check: {sum(gaps.values()) / M_PER_MI:.2f} mi of UA street in NO zone "
          f"({len(gaps)} names) -> ua_zone_gaps.csv")
    print(f"  city check: {sum(outside.values()) / M_PER_MI:.2f} mi of zone street OUTSIDE "
          f"UA limits ({len(outside)} names) -> ua_zone_outside_city.csv")
    return city


def render_overview(G, results, city):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    cols = {"1": "#c8a96e", "2": "#7f8fb5", "3": "#d98ca0", "4": "#9e9e9e"}
    allg = unary_union([g for g, *_ in results.values()] + ([city] if city is not None else []))
    minx, miny, maxx, maxy = allg.bounds
    pad = 0.004
    fig, ax = plt.subplots(figsize=(12, 13))
    for u, v, d in G.edges(data=True):
        xs, ys = zip(*edge_coords(G, u, v, d))
        if max(xs) < minx - pad or min(xs) > maxx + pad or max(ys) < miny - pad or min(ys) > maxy + pad:
            continue
        ax.plot(xs, ys, color="#c8c8c8", lw=0.5, zorder=1)
    if city is not None:
        for poly in polys_of(city):
            xs, ys = poly.exterior.xy
            ax.plot(xs, ys, color="k", lw=1.5, ls="--", zorder=4, label="UA city limit (OSM)")
    for zid, (geom, *_ ) in results.items():
        for poly in polys_of(geom):
            xs, ys = poly.exterior.xy
            ax.fill(xs, ys, color=cols[zid], alpha=0.25, zorder=2)
            ax.plot(xs, ys, color=cols[zid], lw=2, zorder=3)
        c = geom.representative_point()
        ax.annotate(ZONES[zid]["day"].upper(), (c.x, c.y), ha="center", weight="bold", fontsize=14, zorder=6)
    ax.set_xlim(minx - pad, maxx + pad); ax.set_ylim(miny - pad, maxy + pad)
    ax.set_aspect(1 / math.cos(math.radians((miny + maxy) / 2)))
    h, l = ax.get_legend_handles_labels()
    if h:
        ax.legend(h[:1], l[:1], loc="lower right")
    ax.set_title("Upper Arlington collection zones")
    fig.tight_layout(); fig.savefig("ua_zones_overview.png", dpi=130); plt.close(fig)


def write_summary(results):
    rows = [st for *_, st in results.values()]
    with open("ua_route_summary.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        for r in rows:
            w.writerow({k: round(v, 2) if isinstance(v, float) else v for k, v in r.items()})


def write_overview(results):
    kml = simplekml.Kml(name="Upper Arlington collection zones")
    colors = {"1": simplekml.Color.hex("6ea9c8"), "2": simplekml.Color.hex("b58f7f"),
              "3": simplekml.Color.hex("a08cd9"), "4": simplekml.Color.white}   # KML is aabbggrr
    owner = defaultdict(set)
    for zid, (geom, req, _) in results.items():
        f = kml.newfolder(name=ZONES[zid]["name"])
        for poly in polys_of(geom):
            pg = f.newpolygon(name=ZONES[zid]["name"], outerboundaryis=list(poly.exterior.coords))
            pg.style.polystyle.color = simplekml.Color.changealphaint(60, colors[zid])
            pg.style.linestyle.color = colors[zid]
            pg.style.linestyle.width = 3
        for u, v, k, d in req:
            owner[(street_name(d), min(u, v), max(u, v))].add(zid)
    kml.savekmz("ua_zones_overview.kmz")
    shared = defaultdict(lambda: [0.0, set()])
    for (name, *_), zs in owner.items():
        if len(zs) > 1:
            shared[name][1] |= zs
            shared[name][0] += 1
    with open("ua_zone_overlaps.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["street", "segments_in_multiple_zones", "zones"])
        for name, (n, zs) in sorted(shared.items()):
            w.writerow([name, int(n), " & ".join(ZONES[z]["day"].title() for z in sorted(zs))])
    print(f"\nOverview: ua_zones_overview.kmz | {len(shared)} streets fall in >1 zone "
          "-> ua_zone_overlaps.csv (decide which day collects them)")


# ----------------------------------------------------------------------------
def build_geoms(Gu, roads, zids, zone_kml=None):
    geoms = {}
    for z in ZONES:
        if zone_kml and z in zids:
            geoms[z] = polygon_from_kml(zone_kml)
            continue
        print(f"\n[{ZONES[z]['name']}] tracing boundary")
        geoms[z] = unary_union([ring_polygon(Gu, roads, r) for r in ZONES[z]["rings"]])
    city = city_boundary() if CLIP_TO_CITY else None
    if city is None:
        return geoms, None
    from shapely.geometry import Point
    fwd = to_utm
    inv = Transformer.from_crs(UTM, "EPSG:4326", always_xy=True).transform
    cu = shp_transform(fwd, city)
    gu = {z: shp_transform(fwd, g).intersection(cu.buffer(CITY_EDGE_M)) for z, g in geoms.items()}
    left = cu.difference(unary_union(list(gu.values())).buffer(15)).buffer(-8).buffer(8)
    for z, pts in GAP_ASSIGN.items():
        for lat, lon in pts:
            pt = Point(fwd(lon, lat))
            for piece in polys_of(left):
                if piece.contains(pt):
                    gu[z] = unary_union([gu[z], piece.buffer(16)]).intersection(cu.buffer(CITY_EDGE_M))
                    print(f"  pocket at {lat},{lon} ({piece.area / 1e4:.1f} ha) -> zone {z}")
    return {z: shp_transform(inv, g) for z, g in gu.items()}, city


def run_zone(G, Gu, roads, zid, geom, start=None, end=None):
    zone = ZONES[zid]
    print(f"\n===== {zone['name']} =====")
    area = shp_transform(to_utm, geom).area / 1e6
    print(f"  zone area {area:.2f} km^2")
    if area < 0.1:
        sys.exit("ERROR: zone polygon implausibly small - check corners or use --zone-kml.")
    c = geom.representative_point()
    s = nearest_node(G, *start) if start else roads.corner(*zone["start"], (c.x, c.y))
    t = nearest_node(G, *end) if end else roads.corner(*zone["end"], (c.x, c.y))

    boundary = {lab for r in zone["rings"] for lab in r}
    buf = shp_transform(to_utm, geom).buffer(EDGE_BUFFER_M)
    base = Gu
    req = []
    for u, v, k, d in base.edges(keys=True, data=True):
        if {str(h) for h in as_list(d.get("highway"))} & EXCLUDE_HIGHWAY:
            continue
        if not INCLUDE_BOUNDARY_ROADS and road_labels(d) & boundary:
            continue
        if buf.contains(shp_transform(to_utm, LineString(edge_coords(base, u, v, d)))):
            req.append((u, v, k, d))
    if not req:
        sys.exit("ERROR: no streets inside zone.")
    tot = defaultdict(float)
    for *_, d in req:
        tot[street_name(d)] += d["length"]
    with open(f"{ZONES[zid]['day']}_streets.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["street", "centerline_ft"])
        for n in sorted(tot):
            w.writerow([n, round(tot[n] * FT_PER_M)])
    print(f"  {len(tot)} streets, {len(req)} segments, "
          f"{sum(d['length'] for *_, d in req) / M_PER_MI:.2f} mi of street")

    steps = solve_undirected(G, Gu, req, s, t)
    before = turn_stats(G, steps)
    steps = optimize_turns(G, steps, s, t,
                           TURN_COST_RIGHT_BIAS if zone["right_turn_bias"] else TURN_COST)
    after = turn_stats(G, steps)

    from collections import Counter
    key = lambda u, v, d: (frozenset((u, v)), round(d["length"], 1))
    need = Counter(key(u, v, d) for u, v, k, d in req)
    got = Counter(key(u, v, d) for u, v, d in steps if d["service"])
    missing = sum((need - got).values())
    assert steps[0][0] == s and steps[-1][1] == t, "route does not run START -> END"
    print("  coverage:", "OK - every segment covered" if not missing else f"{missing} MISSING")
    stats = write_outputs(G, zid, zone, geom, steps, s, t, before, after)
    stats.update(missing=missing, streets=len(tot), segments=len(req))
    render_png(G, zid, zone, geom, steps, s, t)
    return geom, req, stats


def main(G=None, args=None):
    global INCLUDE_BOUNDARY_ROADS
    INCLUDE_BOUNDARY_ROADS = not args.no_boundary
    if G is None:
        G = load_graph(args.graphml)
    G, Gu = prepare(G)
    roads = Roads(G)
    parse = lambda s: tuple(map(float, s.split(","))) if s else None
    zids = list(ZONES) if args.zone == "all" else [args.zone]
    global CLIP_TO_CITY
    CLIP_TO_CITY = not args.no_city_clip
    geoms, _ = build_geoms(Gu, roads, zids, args.zone_kml if len(zids) == 1 else None)
    results = {}
    for z in zids:
        results[z] = run_zone(G, Gu, roads, z, geoms[z], parse(args.start), parse(args.end))
    write_summary(results)
    if len(zids) > 1:
        write_overview(results)
        city = check_gaps(Gu, results)
        render_overview(G, results, city)
    validate_kmz([f"{ZONES[z]['day']}_route.kmz" for z in zids] +
                 (["ua_zones_overview.kmz"] if len(zids) > 1 else []))


def validate_kmz(files):
    import zipfile
    import xml.etree.ElementTree as ET
    for fn in files:
        with zipfile.ZipFile(fn) as z:
            ET.fromstring(z.read("doc.kml"))
    print(f"KMZ check: {len(files)} file(s) unzip to valid doc.kml")


def cli(argv=None):
    ap = argparse.ArgumentParser(description="Upper Arlington collection routes")
    ap.add_argument("--zone", default="all", choices=list(ZONES) + ["all"])
    ap.add_argument("--no-boundary", action="store_true",
                    help="don't collect on the zone's boundary roads")
    ap.add_argument("--zone-kml", help="use a polygon you drew (Google Earth/My Maps KML)")
    ap.add_argument("--start", help="override start 'lat,lon'")
    ap.add_argument("--end", help="override end 'lat,lon'")
    ap.add_argument("--graphml", help="load a saved OSM graph")
    ap.add_argument("--no-city-clip", action="store_true",
                    help="don't clip zones to the Upper Arlington city limit")
    return ap.parse_args(argv)


if __name__ == "__main__":
    main(args=cli())
