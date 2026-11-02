#!/usr/bin/env python3
"""Turn an OSRM route (steps + geometry) into the driver-app route format."""
import json, sys

MOD = {"left": "left", "right": "right", "slight left": "slight_left", "slight right": "slight_right",
       "sharp left": "sharp_left", "sharp right": "sharp_right", "straight": "straight", "uturn": "uturn"}


def turn_of(man):
    t, m = man["type"], man.get("modifier")
    if t == "depart":
        return "start"
    if t == "arrive":
        return "arrive"
    if t in ("roundabout", "rotary", "roundabout turn"):
        return "roundabout"
    return MOD.get(m, "straight")


def main(src, dst, title, route_id):
    r = json.load(open(src))["routes"][0]
    steps = []
    for s in r["legs"][0]["steps"]:
        man = s["maneuver"]
        name = s.get("name") or s.get("ref") or s.get("destinations") or "unnamed road"
        if man["type"] == "arrive":
            name = "Destination"
        coords = [[round(x, 6), round(y, 6)] for x, y in s["geometry"]["coordinates"]]
        d = dict(turn=turn_of(man), street=name, mode="DRIVE", ft=round(s["distance"] * 3.28084),
                 coords=coords)
        if d["turn"] == "roundabout" and man.get("exit"):
            d["exit"] = man["exit"]
        steps.append(d)
    out = dict(id=route_id, title=title, note="test drive, not a collection route", steps=steps)
    json.dump(out, open(dst, "w"), separators=(",", ":"))
    print(f"{len(steps)} steps, {r['distance'] / 1609.344:.1f} mi -> {dst}")


if __name__ == "__main__":
    main(*sys.argv[1:5])
