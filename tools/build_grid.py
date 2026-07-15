"""Build data/grid.json: every map square's polygon + designation.

Geometry model (fitted from the map scan by line/arc detection):
- The map is a polar fan. Apex (projection center) at APEX (map px).
- Rows A-V are annuli between 23 fitted arc radii (BOUNDS).
- Columns are radial sectors. Base columns 10 (west) .. 21 (east).
  Each base column splits in half at row F, and in half again at row P.
  Suffix 1 = western sub-column, 2 = eastern (e.g. 161 -> 1611, 1612).
- A cell exists if enough of its interior lies on the map (onmap mask).

Run from project root:  python tools/build_grid.py
"""
import json
import math
import os

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAP = os.path.join(ROOT, "NORAD map.jpg")

APEX = (1016.9, 93.85)
BOUNDS = [608.2, 752.1, 905.8, 1060.6, 1211.3, 1364.1, 1515.0, 1667.0,
          1817.6, 1971.5, 2120.7, 2270.9, 2421.8, 2572.1, 2727.2, 2873.5,
          3027.1, 3177.9, 3326.0, 3481.8, 3630.0, 3779.5, 3941.0]
ROWS = "ABCDEFGHIJKLMNOPQRSTUV"

# base column boundary angles, degrees, west -> east (col 10 .. col 21)
BASE = [121.07, 109.52, 99.81, 89.80, 79.85, 69.84, 60.18, 49.94,
        40.00, 30.05, 19.61, 9.23, -1.02]

HALF_MEASURED = {
    (109.52, 99.81): 104.64, (99.81, 89.80): 94.60, (89.80, 79.85): 84.55,
    (79.85, 69.84): 74.50, (69.84, 60.18): 64.80, (60.18, 49.94): 54.91,
    (49.94, 40.00): 44.90, (40.00, 30.05): 35.20, (30.05, 19.61): 24.80,
    (19.61, 9.23): 14.45, (9.23, -1.02): 4.30, (121.07, 109.52): 114.45,
}

# measured quarter-boundary angles (rows P-V)
QMEASURED = [92.08, 87.17, 82.28, 77.28, 72.22, 67.28, 62.42, 57.38, 52.23,
             47.30, 42.27, 37.48, 32.42, 27.32, 22.18, 17.32, 12.22, 7.17,
             2.02]

MIN_INSIDE = 0.55       # fraction of sampled interior that must be on-map

# city squares (detected dot positions verified against the map).
# Point values transcribed from the map itself; where the rules-PDF appendix
# differs, the printed map value is used (LA 6, Jacksonville 8) and Detroit
# (8, missing from the appendix) is included.
CITIES = {
    "G212": ("Godthab", 5), "H121": ("Anchorage", 5), "J152": ("Edmonton", 5),
    "L142": ("Vancouver", 5), "L171": ("Winnipeg", 5), "M142": ("Seattle", 7),
    "M192": ("Quebec", 5), "N172": ("Minneapolis", 5), "N191": ("Toronto", 6),
    "O142": ("Portland", 5), "O171": ("Omaha", 9), "O181": ("Chicago", 9),
    "O182": ("Detroit", 8), "O191": ("Pittsburgh", 7), "O192": ("Boston", 5),
    "P1612": ("Denver", 6), "P1721": ("Kansas City", 6),
    "P1722": ("St. Louis", 5), "P1822": ("Cincinnati", 6),
    "P1912": ("Philadelphia", 5), "P1921": ("New York", 9),
    "Q1422": ("San Francisco", 7), "Q1912": ("Washington, D.C.", 9),
    "R1511": ("Los Angeles", 6), "R1521": ("Phoenix", 5),
    "R1712": ("Dallas", 5), "R1812": ("Birmingham", 6),
    "R1822": ("Atlanta", 5), "R1912": ("Norfolk", 7),
    "S1512": ("San Diego", 8), "S1812": ("Mobile", 6),
    "S1911": ("Savannah", 5), "T1711": ("San Antonio", 5),
    "T1721": ("Houston", 5), "T1811": ("New Orleans", 6),
    "T1822": ("Jacksonville", 8), "U1822": ("Key West", 6),
}

# Canadian cities (matters for the optional Canadian Air Defense rule).
# Godthab is in Greenland - not Canadian.
CANADIAN = {"L142", "J152", "L171", "N191", "M192"}


def half_bounds():
    out = [BASE[0]]
    for a, b in zip(BASE, BASE[1:]):
        out.append(HALF_MEASURED.get((a, b), (a + b) / 2))
        out.append(b)
    return out


def quarter_bounds():
    hb = half_bounds()
    out = [hb[0]]
    for a, b in zip(hb, hb[1:]):
        mid = (a + b) / 2
        best = min(QMEASURED, key=lambda q: abs(q - mid)) if QMEASURED else mid
        out.append(best if abs(best - mid) < 0.7 else mid)
        out.append(b)
    return out


def col_labels(level):
    """(label, west_angle, east_angle) west->east.
    level: 0 = base (rows A-E), 1 = halves (F-O), 2 = quarters (P-V)."""
    labels = []
    if level == 0:
        for i in range(12):
            labels.append((str(10 + i), BASE[i], BASE[i + 1]))
    elif level == 1:
        hb = half_bounds()
        for i in range(12):
            base = str(10 + i)
            labels.append((base + "1", hb[2 * i], hb[2 * i + 1]))
            labels.append((base + "2", hb[2 * i + 1], hb[2 * i + 2]))
    else:
        qb = quarter_bounds()
        for i in range(12):
            base = str(10 + i)
            for j, suf in enumerate(("11", "12", "21", "22")):
                labels.append((base + suf, qb[4 * i + j], qb[4 * i + j + 1]))
    return labels


def cell_poly(r0, r1, a_w, a_e):
    cx, cy = APEX
    steps = max(2, int(abs(a_w - a_e) / 1.5) + 1)

    def pt(r, adeg):
        a = math.radians(adeg)
        return [round(cx + r * math.cos(a), 1), round(cy + r * math.sin(a), 1)]

    poly = [pt(r0, a_w + (a_e - a_w) * i / steps) for i in range(steps + 1)]
    poly += [pt(r1, a_e + (a_w - a_e) * i / steps) for i in range(steps + 1)]
    return poly


def interior_frac(onmap, r0, r1, a_w, a_e, n=6):
    cx, cy = APEX
    h, w = onmap.shape
    hit = tot = 0
    for i in range(1, n):
        for j in range(1, n):
            r = r0 + (r1 - r0) * i / n
            a = math.radians(a_w + (a_e - a_w) * j / n)
            x, y = int(cx + r * math.cos(a)), int(cy + r * math.sin(a))
            if 0 <= x < w and 0 <= y < h:
                tot += 1
                hit += bool(onmap[y, x])
    return hit / tot if tot else 0.0


def build_onmap():
    g = cv2.cvtColor(cv2.imread(MAP), cv2.COLOR_BGR2GRAY)
    bright = (g > 140).astype(np.uint8)
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (121, 121))
    closed = cv2.morphologyEx(bright, cv2.MORPH_CLOSE, k)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(closed)
    base = (lab == 1 + np.argmax(stats[1:, 4]))
    ff = base.astype(np.uint8)
    h, w = ff.shape
    m = np.zeros((h + 2, w + 2), np.uint8)
    for x in range(0, w, 50):
        for y in (0, h - 1):
            if ff[y, x] == 0:
                cv2.floodFill(ff, m, (x, y), 1)
    for y in range(0, h, 50):
        for x in (0, w - 1):
            if ff[y, x] == 0:
                cv2.floodFill(ff, m, (x, y), 1)
    return base | (ff == 0)


def main():
    cache = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "onmap.npy")
    onmap = np.load(cache) if os.path.exists(cache) else build_onmap()

    cells = {}
    for i, row in enumerate(ROWS):
        r0, r1 = BOUNDS[i], BOUNDS[i + 1]
        level = 0 if row <= "E" else (1 if row <= "O" else 2)
        for label, a_w, a_e in col_labels(level):
            frac = interior_frac(onmap, r0, r1, a_w, a_e)
            if frac >= MIN_INSIDE:
                cid = row + label
                cells[cid] = {
                    "poly": cell_poly(r0, r1, a_w, a_e),
                    "row": row, "col": label,
                }
                if cid in CITIES:
                    name, pts = CITIES[cid]
                    cells[cid]["city"] = {"name": name, "points": pts,
                                          "canadian": cid in CANADIAN}
    missing = [c for c in CITIES if c not in cells]
    if missing:
        raise SystemExit(f"city cells missing from grid: {missing}")
    os.makedirs(os.path.join(ROOT, "data"), exist_ok=True)
    with open(os.path.join(ROOT, "data", "grid.json"), "w") as f:
        json.dump({"apex": APEX, "cells": cells}, f)
    print(f"{len(cells)} cells written to data/grid.json "
          f"({len(CITIES)} city squares)")


if __name__ == "__main__":
    main()
