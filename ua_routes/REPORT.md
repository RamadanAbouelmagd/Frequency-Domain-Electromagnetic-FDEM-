# Upper Arlington collection routes: run report

Data: OpenStreetMap, downloaded 2026-10-31. Overpass was unreachable from the cloud
container, so `fetch_osm.py` pulled the same OSM data from api.openstreetmap.org and
built the graph with osmnx's own `graph_from_bbox` pipeline (filter `network_type="drive"`).
The snapshot is committed as `ua_osm_graph.graphml` + `ua_city_boundary.geojson`, so
`python ua_routes.py --zone all` re-runs offline and produces identical output.

## Results

Zones are named by collection day, matching the city's collection-day map (updated 3/08).
Files: `<day>_route.kmz`, `<day>_route.gpx`, `<day>_turn_sheet.csv`, `<day>_streets.csv`,
`<day>_preview.png` for day = monday / tuesday / wednesday / thursday.

| Day | Total mi | Collect mi | Alleys mi | Repeat mi | Repeat % | Est. h (8/22 mph) | Right | Straight | Left | U-turn | Red legs |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Monday (right bias) | 44.53 | 37.97 | 0.19 | 6.56 | 14.7 | 5.0 | 154 | 271 | 95 | 30 | 13 (≤257 ft) |
| Tuesday | 44.12 | 38.01 | 0.37 | 6.11 | 13.8 | 5.0 | 127 | 218 | 88 | 16 | 1 (98 ft) |
| Wednesday | 56.26 | 45.03 | 0.00 | 11.23 | 20.0 | 6.1 | 181 | 242 | 136 | 46 | 3 (≤93 ft) |
| Thursday | 65.09 | 48.71 | 0.14 | 16.38 | 25.2 | 6.8 | 213 | 236 | 147 | 79 | 0 |

- Coverage: **OK** for every day (strict check, counts parallel segments).
- Every route starts at its START corner and ends at its END corner (asserted).
- All KMZs unzip to valid `doc.kml`.
- Unzoned city streets: 0.38 mi of slivers only (Lane Ave east of North Star, Zollinger stub,
  Scioto Pointe stub).

## Fixes made for the real street network
| Problem found | Fix |
|---|---|
| North Star becomes **North Star Avenue** south of ~39.994 and is the road that reaches 5th Ave; the pattern only matched "Road", so the 5th Ave corner was ~530 m off and Old Arlington (Cambridge, Arlington, Andover, Concord…; 2.25 mi) fell into no zone | Pattern `north\s*star\s+(r\|av)` |
| **Tremont never reaches 5th Ave** (it ends ~0.3 mi north) | Zone 1 area A ring now = Canterbury, Tremont, **Lane**, Riverside (B already covers everything south of Lane; same intended area, no fake corner) |
| **Reed ends at Fishinger ~800 m west of Tremont**; **North Star ends at Fishinger ~300 m west of Kenny** | Zone 3 ring runs along Fishinger at both joins: Henderson, Kenny, Fishinger, North Star, Lane, Tremont, Fishinger, Reed |
| `henderson` matched **Old Henderson Rd** and **Henderson Heights Rd** | Pattern `(?<!old )henderson\s+r(oa)?d` |
| Columbus enclaves inside the road rings (Ducrest, Norwell, Larwell, Reymond, Trentwood, Winterset, Old Henderson; 2.3 mi) | Zones clipped to the OSM Upper Arlington city limit (30 m edge tolerance). `--no-city-clip` disables |
| One-way legs driven wrong way, incl. Mount Holyoke 1,827 ft, Brandon 1,579 ft, S. Mallway 642 ft | New zero-mile step: reverse a directed cycle of two-way route arcs through each wrong-way arc. Long ones all fixed; 14 short island slip-lane legs remain flagged red |

## Remaining warnings (inspected on the maps, all acceptable)
- `canterbury`/`tremont` don't intersect (~90 m): Canterbury's east end becomes **Redding Rd**
  for 90 m before Tremont. The corner sits at Canterbury/Redding. Zone 2's END is there.
- `tremont`, `north_star`, `riverside`, `canterbury` "not continuous between corners":
  short unnamed/differently named links at junction islands; fallback path follows the
  real road (checked on `diag_south.png`, `diag_north.png`).

## Red legs still flagged (all ≤ 252 ft, traffic-island slip lanes)
Zone 1: Arlington Ave ×2 (island), Redding Rd, Abington Rd, 3 unnamed links.
Zone 2: Tremont Rd, Redding Rd (Tremont/Redding/Canterbury triangle).
Zone 3: Tremont ×2, Redding ×2 (same triangle), La Rochelle Dr 48 ft.
Driver: go around the island the legal way.

## Pockets assigned from the city map
- East of Kenny Rd between McCoy and Fishinger (Criswell, Millcreek, Kenbrook Common, Highland,
  Regency, Kennybrook, Kingsdale Terrace; 1.7 mi): **Wednesday**, as shaded on the city map.
- North of Henderson by US-33 (Tarrington, Bethel, Horizons; 0.9 mi): **Thursday**. It is not
  drawn on the city map; Thursday is its only neighbour.
- Wednesday's north edge = Henderson / city limit, as on the city map (not McCoy).

## One day per street (`ua_boundary_assignment.csv`)
Every street segment is collected on exactly one day (`ua_zone_overlaps.csv` is empty).
Rule: each zone is first solved without the shared boundary roads; each shared stretch
(same street, same pair of days) then goes to the day whose route already drives most of
it, so it costs the least extra driving.

| Street | Borders | Collected on |
|---|---|---|
| Canterbury Rd | Mon / Tue | **Tuesday** |
| Fishinger Rd | Tue / Wed and Tue / Thu | **Tuesday** |
| Reed Rd | Wed / Thu | **Wednesday** |
| Tremont Rd, Canterbury to Fishinger | Tue / Wed | **Tuesday** |
| Tremont Rd, south of Canterbury (872 ft) | Mon / Wed | **Wednesday** |
| Lane Ave | Mon / Wed | **Wednesday** |
| Redding Rd | Mon / Tue / Wed | Monday (409 ft), Wednesday (184 ft) |

To override, run with `--both-days` (old behaviour) or edit the CSV logic in `assign_shared`.

## Alleys
Collected: `highway=service` + `service=alley` (driveways, parking aisles, private ways
excluded). OpenStreetMap only has **0.7 mi** of alleys tagged in Upper Arlington (Mon 988 ft,
Tue 1,968 ft, Thu 744 ft). If the trucks know of alleys that are missing, they are untagged
in OSM; add them on openstreetmap.org (or send me a list) and re-run `fetch_osm.py`.

## Still open
- **Monday END** (5th Ave & North Star Ave) is ~0.25 mi south of the city limit; the last leg
  is a repeat drive to that corner.
