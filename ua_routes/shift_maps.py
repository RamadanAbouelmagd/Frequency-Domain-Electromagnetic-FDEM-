"""Render one PNG per day: the 2-shift and 3-shift splits side by side."""
import csv, glob, math, xml.etree.ElementTree as ET
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
NS = {"g": "http://www.topografix.com/GPX/1/1"}
COL = ["#1f77b4", "#d62728", "#2ca02c"]
for day in ["monday", "tuesday", "wednesday", "thursday"]:
    fig, axes = plt.subplots(1, 2, figsize=(16, 9))
    for ax, n in zip(axes, (2, 3)):
        for k in range(1, n + 1):
            r = ET.parse(f"shifts/{day}_{n}shifts_part{k}.gpx").getroot()
            pts = [(float(p.get("lon")), float(p.get("lat"))) for p in r.iterfind(".//g:trkpt", NS)]
            xs, ys = zip(*pts)
            ax.plot(xs, ys, color=COL[k - 1], lw=1.3, label=f"Shift {k}")
            ax.scatter([xs[0]], [ys[0]], s=140, color=COL[k - 1], edgecolor="k", zorder=5)
            ax.annotate(f"S{k} start", (xs[0], ys[0]), xytext=(6, 6), textcoords="offset points",
                        weight="bold", fontsize=9)
        ax.set_title(f"{day.title()} - {n} shifts"); ax.legend(loc="lower left")
        ax.set_aspect(1 / math.cos(math.radians(ys[0]))); ax.set_xticks([]); ax.set_yticks([])
    fig.tight_layout(); fig.savefig(f"shifts/{day}_shift_split.png", dpi=110); plt.close(fig)
