"""Build assets/units/ from the counter-sheet scans.

Pipeline:
1. Cut each scan into 146x146 cells (grid measured from the scans).
2. For each unit TYPE, pick the cleanest specimen (least residual cut-line
   ink near the borders) and rebuild it as a canonical tile: silhouette ink
   composited onto a flat sheet-colored background.
3. Every unit of a type shares the same tile, so decoys are pixel-identical
   to real units (no information leaks from scan artifacts).

Unit roster (per owner's physical set + rules):
- American: 12 fighters, 5 missiles, 4 decoy fighters.
  Real units' backs = explosion (American_Canadian_explosion.png);
  decoy backs = blank blue.
- Canadian (optional rules): 3 fighters, 1 missile, 1 decoy fighter.
- Soviet: 23 bombers, 7 decoy bombers, 5 missiles (optional).
  Real backs = mushroom cloud; decoy backs = blank pink.
  The back-side scan is MIRRORED relative to the front scan.

Run from project root:  python tools/extract_units.py
"""
import json
import os

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "assets", "units")
CELL = 146


def us_grid():
    row_y = {1: 193, 2: 352, 3: 550, 4: 701, 5: 899, 6: 1049, 7: 1247}
    top = [139.5, 289.0, 441.0, 593.5, 742.0]
    bot = [131.0, 282.5, 434.0, 586.0, 738.5]
    return _grid(row_y, top, bot, 193, 1049)


def sov_grid(back=False):
    if not back:
        row_y = {1: 208, 2: 358, 3: 558, 4: 708, 5: 909, 6: 1056, 7: 1259}
        top = [126.5, 278.5, 429.5, 578.5, 729.5]
        bot = [131.5, 284.0, 434.5, 587.0, 735.5]
        return _grid(row_y, top, bot, 208, 1056)
    row_y = {1: 208, 2: 356, 3: 554, 4: 704, 5: 907, 6: 1055, 7: 1256}
    top = [118.0, 265.0, 414.5, 562.0, 712.0]
    bot = [122.0, 270.0, 420.0, 568.0, 716.0]
    return _grid(row_y, top, bot, 208, 1055)


def _grid(row_y, top, bot, y0, y1):
    g = {}
    for r, y in row_y.items():
        t = (y - y0) / (y1 - y0)
        for c in range(1, 6):
            g[(r, c)] = (top[c - 1] * (1 - t) + bot[c - 1] * t, y)
    return g


def crop(im, cx, cy):
    half = CELL // 2
    x0 = max(0, min(im.shape[1] - CELL, int(round(cx - half))))
    y0 = max(0, min(im.shape[0] - CELL, int(round(cy - half))))
    return im[y0:y0 + CELL, x0:x0 + CELL]


def clean_ink(bgr):
    """Return (keep_mask, border_score) - silhouette ink minus cut lines."""
    g = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    ink = (g < 120).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(ink)
    keep = np.zeros_like(ink, bool)
    H, W = ink.shape
    for i in range(1, n):
        x, y, w, h, a = stats[i]
        if a < 25:
            continue
        if (max(w, h) > 6 * min(w, h)) or a / (w * h) < 0.15:
            continue
        if (x == 0 or y == 0 or x + w >= W or y + h >= H) and a < 500:
            continue
        keep |= (lab == i)
    border = keep.copy()
    border[6:-6, 6:-6] = False
    return keep, int(border.sum())


def tile_from(bgr, keep, bg_rgb):
    g = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY).astype(float)
    tile = np.zeros((CELL, CELL, 3), np.uint8)
    tile[:, :] = bg_rgb[::-1]
    alpha = np.clip((150 - g) / 150, 0, 1) * keep
    for c in range(3):
        tile[:, :, c] = (tile[:, :, c] * (1 - alpha) + 20 * alpha).astype(
            np.uint8)
    return tile


def best_tile(crops, bg_rgb, min_ink):
    scored = []
    for c in crops:
        keep, border = clean_ink(c)
        scored.append((border, -keep.sum(), c, keep))
    scored.sort(key=lambda s: (s[0], s[1]))
    border, neg_ink, c, keep = scored[0]
    if -neg_ink < min_ink:
        raise SystemExit("silhouette lost during cleaning")
    return tile_from(c, keep, bg_rgb)


def flat(bg_rgb):
    t = np.zeros((CELL, CELL, 3), np.uint8)
    t[:, :] = bg_rgb[::-1]
    return t


def sheet_bg(bgr):
    med = np.median(bgr.reshape(-1, 3), axis=0)
    return tuple(int(v) for v in med[::-1])


def main():
    os.makedirs(OUT, exist_ok=True)
    us = cv2.imread(os.path.join(ROOT, "US_Canada_units.png"))
    sf = cv2.imread(os.path.join(ROOT, "Soviet_units_front.png"))
    sb = cv2.imread(os.path.join(ROOT, "Soviet_units_back.png"))
    expl = cv2.imread(os.path.join(ROOT, "American_Canadian_explosion.png"))

    blue, pink = sheet_bg(us), sheet_bg(sf)
    gu, gf, gb = us_grid(), sov_grid(False), sov_grid(True)

    fighters = [crop(us, *gu[(r, c)]) for r, cols in
                ((2, range(1, 6)), (3, range(1, 6)), (4, range(1, 3)))
                for c in cols]
    tiles = {
        "us_fighter": best_tile(fighters, blue, 1500),
        "us_missile": best_tile([crop(us, *gu[(1, c)]) for c in range(1, 6)],
                                blue, 1200),
        "ca_fighter": best_tile([crop(us, *gu[(6, c)]) for c in (1, 2, 3)],
                                blue, 1500),
        "ca_missile": best_tile([crop(us, *gu[(6, 4)])], blue, 1200),
        "sov_bomber": best_tile([crop(sf, *gf[(r, c)]) for r in range(1, 5)
                                 for c in range(1, 6)], pink, 1500),
        "sov_missile": best_tile([crop(sf, *gf[(7, c)]) for c in range(1, 6)],
                                 pink, 700),
        "cloud": best_tile([crop(sb, *gb[(r, c)]) for r in range(1, 5)
                            for c in range(1, 6)], pink, 1500),
    }
    e = cv2.resize(expl, (CELL, CELL), interpolation=cv2.INTER_CUBIC)
    keep, _ = clean_ink(e)
    tiles["explosion"] = tile_from(e, keep, blue)

    manifest = []

    def save(img, name, **meta):
        cv2.imwrite(os.path.join(OUT, name + ".png"), img)
        manifest.append({"id": name, "file": name + ".png", **meta})

    cv2.imwrite(os.path.join(OUT, "us_back_blank.png"), flat(blue))
    cv2.imwrite(os.path.join(OUT, "us_back_real.png"), tiles["explosion"])
    cv2.imwrite(os.path.join(OUT, "sov_back_blank.png"), flat(pink))
    cv2.imwrite(os.path.join(OUT, "sov_back_real.png"), tiles["cloud"])

    for i in range(1, 6):
        save(tiles["us_missile"], f"us_missile_{i}", side="us",
             kind="missile", real=True, move=0, back="us_back_real.png")
    for i in range(1, 13):
        save(tiles["us_fighter"], f"us_fighter_{i}", side="us",
             kind="fighter", real=True, move=6, back="us_back_real.png")
    for i in range(1, 5):
        save(tiles["us_fighter"], f"us_decoy_{i}", side="us",
             kind="decoy_fighter", real=False, move=6,
             back="us_back_blank.png")

    for i in range(1, 4):
        save(tiles["ca_fighter"], f"ca_fighter_{i}", side="us",
             kind="fighter", real=True, move=6, canadian=True, optional=True,
             back="us_back_real.png")
    save(tiles["ca_missile"], "ca_missile_1", side="us", kind="missile",
         real=True, move=0, canadian=True, optional=True,
         back="us_back_real.png")
    save(tiles["ca_fighter"], "ca_decoy_1", side="us", kind="decoy_fighter",
         real=False, move=6, canadian=True, optional=True,
         back="us_back_blank.png")

    for i in range(1, 24):
        save(tiles["sov_bomber"], f"sov_bomber_{i}", side="soviet",
             kind="bomber", real=True, move=4, back="sov_back_real.png")
    for i in range(1, 8):
        save(tiles["sov_bomber"], f"sov_decoy_{i}", side="soviet",
             kind="decoy_bomber", real=False, move=4,
             back="sov_back_blank.png")
    for i in range(1, 6):
        save(tiles["sov_missile"], f"sov_missile_{i}", side="soviet",
             kind="missile", real=True, move=1, optional=True,
             back="sov_back_real.png")

    with open(os.path.join(OUT, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1)
    print(f"{len(manifest)} units written to {OUT}")


if __name__ == "__main__":
    main()
