# Task brief: Upper Arlington collection routes (Zones 1–4)

## Goal
Generate GPS-accurate, single-pass driving routes for a weekly collection business in
Upper Arlington, Ohio. One route per zone. Each route must drive **every street in the
zone at least once**, in **one direction only** (no second pass for the other side of
the street), with the least possible repeat driving, as **one continuous path** from a
fixed start corner to a fixed end corner.

Deliver a KMZ per zone (plus GPX and a driver turn sheet) and an overview map of all
zones.

**Do not hand-type or "recall" coordinates.** All geometry must come from
OpenStreetMap via `osmnx`. The script `ua_routes.py` (provided alongside this brief)
already implements the full pipeline. Your job: run it on real data, validate the
results, fix anything the real street network breaks, and report back.

---

## Zones

| Zone | Day | Boundaries | Start (NW) | End (SE) | Turn preference |
|---|---|---|---|---|---|
| 1 | Monday | **Two areas merged:** (A) south of Canterbury Rd AND west of Tremont Rd, down to 5th Ave, east of Riverside Dr; (B) south of Lane Ave down to 5th Ave, between Riverside Dr (W) and North Star Rd (E) | Canterbury Rd & Riverside Dr | 5th Ave & North Star Rd | **Favor right turns** (strong bias) |
| 2 | Tuesday | North of Canterbury Rd, south of Fishinger Rd, west of Tremont Rd, east of Riverside Dr (US-33) | Fishinger Rd & Riverside Dr | Canterbury Rd & Tremont Rd | Standard |
| 3 | Wednesday | North of Lane Ave, east of Tremont Rd and Reed Rd, west of Kenny Rd / North Star Rd, south of Henderson Rd | Henderson Rd & Reed Rd | Lane Ave & North Star Rd | Standard |
| 4 | Thursday | North of Fishinger Rd, west of Reed Rd, south of Henderson Rd, east of Riverside Dr (US-33) | Henderson Rd & Riverside Dr | Fishinger Rd & Reed Rd | Standard |

### How the boundaries are encoded in `ua_routes.py`
Each zone is one or more **rings**: boundary roads listed in order around the zone
(north → east → south → west). Corners are where consecutive roads intersect. The
polygon is traced **along the actual road centerlines** between corners.

```
Zone 1: ring A [canterbury, tremont, fifth, riverside]  ∪  ring B [lane, north_star, fifth, riverside]
Zone 2: [fishinger, tremont, canterbury, riverside]
Zone 3: [henderson, kenny, north_star, lane, tremont, reed]
Zone 4: [henderson, reed, fishinger, riverside]
```

Road-name regexes (matched case-insensitively against OSM `name` + `ref`):

| key | pattern |
|---|---|
| henderson | `\bhenderson\b` |
| reed | `\breed\s+r(oa)?d\b` |
| fishinger | `\bfishinger\b` |
| riverside | `\briverside\s+dr\|\bus\s*33\b\|\b(oh\|sr)\s*257\b` |
| canterbury | `\bcanterbury\s+r(oa)?d\b` |
| tremont | `\btremont\s+r(oa)?d\b` |
| lane | `\blane\s+av` |
| north_star | `\bnorth\s*star\s+r` |
| fifth | `\b(5th\|fifth)\s+av` |
| kenny | `\bkenny\s+r(oa)?d\b` |

### Assumptions to verify (the zone descriptions were written loosely)
1. **Zone 3 north edge = Henderson Rd.** The description gave no north boundary; the
   start corner was described as "near Reed Rd and McCoy Rd/Henderson Rd". If the real
   map suggests McCoy Rd is the intended edge, flag it to the user. Don't decide alone.
2. **Zone 1 area A's south edge = 5th Ave.** Area A ("south of Canterbury, west of
   Tremont") had no stated south edge; 5th Ave was used to match area B.
3. **Corner intersections may not exist in OSM** (e.g., Tremont may not meet 5th Ave,
   Reed may not meet Tremont directly, North Star may not meet Kenny, Riverside may
   not meet 5th Ave). The script then uses the nearest points and prints a
   `WARNING ... don't intersect` line. Every such warning must be inspected on the map.
4. **Riverside Dr may be split into two one-way carriageways in OSM.** The corner
   picker chooses the candidate node nearest the zone interior; confirm the polygon
   doesn't cut off or include the wrong carriageway.
5. **The intended zones should tile Upper Arlington with no gaps**, sharing boundary
   roads. Check the overview map for gaps or overlaps between zones.

---

## Routing method (already implemented; keep it)
1. **Network:** `osmnx.graph_from_bbox` with `network_type="drive"`, bbox
   `(west=-83.130, south=39.970, east=-83.020, north=40.090)` covering all of UA.
   Cached as `ua_osm_graph.graphml`. Keep the largest strongly connected component.
2. **Required streets:** every undirected street segment whose geometry lies inside
   the zone polygon (12 m buffer, in UTM 17N / EPSG:32617). Excluded highway types:
   `motorway, motorway_link, trunk_link, service, services` (so driveways, parking
   aisles and alleys are out by default). Boundary roads are included unless
   `--no-boundary`.
3. **Single-pass Rural Chinese Postman (undirected):**
   - Connect disconnected pieces of the required street set with shortest paths via
     an MST.
   - Flip parity of START and END so the result is an open path START → END.
   - Pair the odd-degree intersections with `networkx.min_weight_matching` on
     shortest-path distances (sparse 25-nearest-neighbor graph first, full graph
     as fallback).
   - Eulerian path from START (`networkx.eulerian_path`).
4. **One-way repair:** repeat/connector runs that would go against a one-way are
   re-routed with a legal directed shortest path. Collection legs that still go
   against a one-way are **flagged** (red in KMZ, noted in the turn sheet). Don't
   silently drop them.
5. **Turn smoothing (zero extra miles):** at each intersection, pair incoming and
   outgoing legs by minimum turn cost (Hungarian / `scipy.optimize.linear_sum_assignment`),
   then merge the resulting cycles greedily by cheapest swap. Turn costs:
   - Standard: straight 0, right 1, left 3, U-turn 8
   - Zone 1 (right-turn bias): straight 0, right 1, left 6, U-turn 12
6. **Speeds for time estimates:** 8 mph collecting, 22 mph repeat/connector driving.
   Configurable at the top of the script (`SERVICE_MPH`, `DEADHEAD_MPH`).

**Not wanted:** driving both sides of the street / directed (both-directions)
coverage. The user explicitly wants **one pass per street**.

---

## Setup & run
```bash
python -m venv .venv && source .venv/bin/activate      # optional
pip install osmnx networkx simplekml shapely pyproj scipy matplotlib
python ua_routes.py --zone all
```
Other options:
```bash
python ua_routes.py --zone 3                        # one zone
python ua_routes.py --zone all --no-boundary        # don't collect on boundary roads
python ua_routes.py --zone 3 --zone-kml zone3.kml   # use a hand-drawn polygon instead
python ua_routes.py --zone 2 --start 40.03,-83.09 --end 40.02,-83.07   # override corners
python ua_routes.py --graphml ua_osm_graph.graphml  # offline re-run
```

## Outputs (per zone N)
| File | Contents |
|---|---|
| `zoneN_route.kmz` | Zone outline (yellow), one continuous route LineString (blue), numbered anchor Points at each street change/turn with an instruction, colour-coded legs folder (green = collect, orange = repeat, red = one-way against traffic), START/END markers |
| `zoneN_route.gpx` | Same route as a GPX track for phone navigation (OsmAnd / Organic Maps) |
| `zoneN_turn_sheet.csv` | step, instruction (Turn RIGHT onto…), street, mode (COLLECT/repeat), feet, cumulative miles, lat, lon, warning |
| `zoneN_streets.csv` | Every street in the zone with centerline length (ft) |
| `ua_zones_overview.kmz` | All 4 zone polygons, colour-coded |
| `ua_zone_overlaps.csv` | Streets falling in more than one zone (shared boundary roads) |

---

## Validation checklist (do all of these)
1. **Console warnings:** list every `WARNING` (corner fallback, discontinuous road) and
   explain what happened for each.
2. **Visual check:** render a PNG per zone with matplotlib (or `osmnx.plot_graph`) showing
   the full street network in grey, zone polygon, route, START/END. Also render the
   overview of all 4 zones together. Look for:
   - polygon edges that cut across blocks instead of following the boundary roads,
   - gaps or overlaps between adjacent zones,
   - streets visibly inside a zone but not on the route.
3. **Coverage:** the script prints `coverage: OK` per zone. Anything else is a failure.
4. **Start/end:** confirm the first route point is the START corner and the last is the
   END corner (script asserts this).
5. **Sanity of the street lists:** spot-check `zoneN_streets.csv` against known UA
   streets; make sure no Columbus streets outside the city limits slipped in (e.g.,
   east of North Star/Kenny or across the Scioto River).
6. **One-way flags:** report the count and street names of any red legs.
7. **Repeat %:** report repeat miles as % of total per zone. Typical for suburban
   networks with cul-de-sacs: 15–30%. If >35%, investigate (usually a bad polygon
   or a long connector caused by a missing corner).
8. **Open in Google Earth** (or confirm the KMZ parses: unzip → `doc.kml` is valid XML).

## When to stop and ask the user
- Any zone boundary that is ambiguous on the real map (especially Zone 3's north edge
  and Zone 1's two-area merge).
- Gaps between zones (streets in no zone) or large overlaps beyond shared boundary roads.
- Which zone should collect the shared boundary roads in `ua_zone_overlaps.csv`
  (Tremont, Lane, Canterbury, Fishinger, Reed, etc.); or whether to run `--no-boundary`.
- Whether alleys (`highway=service`) should be collected; they're excluded by default.

If a zone can't be traced reliably from road names, the fallback is: ask the user to
draw the polygon in Google Earth / Google My Maps, export KML, and run with `--zone-kml`.

## Acceptance criteria
- 4 KMZ + 4 GPX + 4 turn sheets + 4 street lists + overview KMZ + overlaps CSV produced.
- `coverage: OK` for all four zones.
- Each route starts and ends at the specified corners.
- Zone polygons follow the real boundary roads and tile without unexplained gaps.
- A short report: per-zone total miles, collect miles, repeat %, estimated hours,
  turn counts (right/straight/left/U-turn), one-way flags, warnings and how each was
  handled, plus the PNG previews.
