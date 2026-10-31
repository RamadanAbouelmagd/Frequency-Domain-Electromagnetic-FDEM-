#!/usr/bin/env python3
"""
Fallback downloader for when the Overpass API is unreachable.

Pulls the same OpenStreetMap data (plus highway=service + service=alley) from the main OSM API (/api/0.6/map) in
small tiles, applies osmnx's own network_type="drive" way filter, then runs
osmnx.graph_from_bbox with its Overpass download step swapped for these
tiles - so truncation, simplification and attributes are identical to a
normal osmnx download. Writes ua_osm_graph.graphml (the cache ua_routes.py
loads automatically).

Usage:  python fetch_osm.py
"""
import json
import os
import re
import time
import xml.etree.ElementTree as ET

import osmnx as ox
import requests

from ua_routes import BBOX, GRAPH_CACHE

API = "https://api.openstreetmap.org/api/0.6/map"
RAW = "ua_osm_raw.json"
PAD = 0.008                     # ~700 m beyond BBOX (osmnx buffers 500 m)
STEP = 0.025
UA = {"User-Agent": "ua_routes/1.0 (collection route planning; osmnx)"}

# osmnx "drive" filter, written as (key, regex) pairs that must NOT match
EXCLUDE = [
    ("area", r"yes"),
    ("access", r"private"),
    ("highway", r"abandoned|bridleway|bus_guideway|construction|corridor|cycleway|elevator|"
                r"escalator|footway|no|path|pedestrian|planned|platform|proposed|raceway|"
                r"razed|rest_area|service|services|steps|track"),
    ("motor_vehicle", r"no"),
    ("motorcar", r"no"),
    ("service", r"alley|driveway|emergency_access|parking|parking_aisle|private"),
]


def keep(tags):
    if tags.get("highway") == "service" and tags.get("service") == "alley":
        rest = [(k, p) for k, p in EXCLUDE if k not in ("highway", "service")]
        return not any(k in tags and re.search(p, tags[k]) for k, p in rest)
    return "highway" in tags and not any(k in tags and re.search(p, tags[k]) for k, p in EXCLUDE)


def fetch_tile(w, s, e, n, depth=0):
    for attempt in range(5):
        r = requests.get(API, params={"bbox": f"{w},{s},{e},{n}"}, headers=UA, timeout=180)
        if r.status_code == 200:
            return [ET.fromstring(r.content)]
        if r.status_code == 400 and depth < 4:          # too many nodes -> split
            mx, my = (w + e) / 2, (s + n) / 2
            return [x for q in ((w, s, mx, my), (mx, s, e, my), (w, my, mx, n), (mx, my, e, n))
                    for x in fetch_tile(*q, depth + 1)]
        print(f"    HTTP {r.status_code}, retry {attempt + 1}")
        time.sleep(2 ** attempt * 2)
    raise RuntimeError(f"tile {w},{s},{e},{n} failed")


def download():
    if os.path.exists(RAW):
        return json.load(open(RAW))
    w0, s0, e0, n0 = (BBOX["west"] - PAD, BBOX["south"] - PAD,
                      BBOX["east"] + PAD, BBOX["north"] + PAD)
    nodes, ways = {}, {}
    xs = [w0 + i * STEP for i in range(int((e0 - w0) / STEP) + 2) if w0 + i * STEP < e0]
    ys = [s0 + j * STEP for j in range(int((n0 - s0) / STEP) + 2) if s0 + j * STEP < n0]
    tiles = [(x, y, min(x + STEP, e0), min(y + STEP, n0)) for x in xs for y in ys]
    for i, t in enumerate(tiles, 1):
        print(f"  tile {i}/{len(tiles)} {tuple(round(v, 3) for v in t)}")
        for root in fetch_tile(*t):
            for el in root:
                tags = {c.get("k"): c.get("v") for c in el.findall("tag")}
                if el.tag == "node":
                    nodes[int(el.get("id"))] = dict(type="node", id=int(el.get("id")),
                                                    lat=float(el.get("lat")),
                                                    lon=float(el.get("lon")), **({"tags": tags} if tags else {}))
                elif el.tag == "way" and keep(tags):
                    ways[int(el.get("id"))] = dict(type="way", id=int(el.get("id")),
                                                   nodes=[int(nd.get("ref")) for nd in el.findall("nd")],
                                                   tags=tags)
        time.sleep(1)
    used = {n for w in ways.values() for n in w["nodes"]}
    missing = used - nodes.keys()
    if missing:
        print(f"  {len(missing)} way nodes outside tiles - dropped from their ways")
        for w in ways.values():
            w["nodes"] = [n for n in w["nodes"] if n in nodes]
    data = {"elements": [nodes[n] for n in used if n in nodes] + list(ways.values())}
    json.dump(data, open(RAW, "w"))
    print(f"  {len(ways)} drivable ways, {len(used)} nodes")
    return data


def main():
    data = download()
    ox._overpass._download_overpass_network = lambda *a, **k: iter([data])
    G = ox.graph_from_bbox((BBOX["west"], BBOX["south"], BBOX["east"], BBOX["north"]),
                           network_type="drive", simplify=True, truncate_by_edge=True)
    ox.save_graphml(G, GRAPH_CACHE)
    print(f"Saved {GRAPH_CACHE}: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")


if __name__ == "__main__":
    main()
