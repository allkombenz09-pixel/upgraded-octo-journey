"""Stylised maps from world-atlas (Natural Earth 1:50m, TopoJSON)."""
import json
import math
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFilter

from . import style
from .style import ASSETS

# lon, lat
PLACES = {
    "Agunnaryd": (14.13, 56.72),
    "Elmtaryd": (14.10, 56.75),
    "Almhult": (14.14, 56.55),
    "Älmhult": (14.14, 56.55),
    "Smaland": (14.6, 57.2),
    "Stockholm": (18.07, 59.33),
    "Oslo": (10.75, 59.91),
    "Spreitenbach": (8.37, 47.42),
    "Munich": (11.58, 48.14),
    "Poland": (19.1, 52.1),
    "East Germany": (12.6, 52.0),
}
LABELS = {"Almhult": "Älmhult", "Smaland": "Småland"}

# lon_min, lon_max, lat_min, lat_max
REGIONS = {
    "sweden": (6.0, 30.0, 54.6, 69.4),
    "scandinavia": (2.0, 33.0, 53.0, 71.0),
    "europe": (-12.0, 36.0, 35.0, 71.0),
    "world": (-170.0, 190.0, -58.0, 80.0),
}
# Countries tinted on each region by default (the story's geography).
DEFAULT_HIGHLIGHT = {"sweden": ["Sweden"], "scandinavia": ["Sweden"], "europe": ["Sweden"], "world": ["Sweden"]}


@lru_cache(maxsize=1)
def countries():
    """Decode the TopoJSON countries object into {name: [rings of (lon, lat)]}."""
    topo = json.loads((ASSETS / "geo" / "countries-50m.json").read_text())
    (sx, sy), (tx, ty) = topo["transform"]["scale"], topo["transform"]["translate"]
    arcs = []
    for arc in topo["arcs"]:
        x = y = 0
        pts = []
        for dx, dy in arc:
            x += dx
            y += dy
            pts.append((x * sx + tx, y * sy + ty))
        arcs.append(pts)

    def ring(idx):
        pts = []
        for i in idx:
            seg = arcs[i] if i >= 0 else arcs[~i][::-1]
            pts.extend(seg if not pts else seg[1:])
        # unwrap rings that cross the antimeridian (Russia, Fiji…) so they don't smear across the map
        out, shift = [], 0.0
        for k, (lon, lat) in enumerate(pts):
            if k:
                d = lon + shift - out[-1][0]
                if d > 180:
                    shift -= 360
                elif d < -180:
                    shift += 360
            out.append((lon + shift, lat))
        return out

    out = {}
    for g in topo["objects"]["countries"]["geometries"]:
        name = g.get("properties", {}).get("name", "")
        polys = g["arcs"] if g["type"] == "MultiPolygon" else [g["arcs"]] if g["type"] == "Polygon" else []
        out.setdefault(name, []).extend(ring(r) for poly in polys for r in poly[:1])  # outer rings only
    return out


class Projection:
    """Equirectangular with cos(lat0) x-scaling, fitted into a pixel box."""

    def __init__(self, region, width, height, pad=0.04):
        lon0, lon1, lat0, lat1 = REGIONS[region]
        self.k = math.cos(math.radians((lat0 + lat1) / 2)) if region != "world" else 0.82
        span_x = (lon1 - lon0) * self.k
        span_y = lat1 - lat0
        scale = min(width * (1 - 2 * pad) / span_x, height * (1 - 2 * pad) / span_y)
        self.scale = scale
        self.lon_c = (lon0 + lon1) / 2
        self.lat_c = (lat0 + lat1) / 2
        self.cx, self.cy = width / 2, height / 2

    def __call__(self, lon, lat):
        return (self.cx + (lon - self.lon_c) * self.k * self.scale,
                self.cy - (lat - self.lat_c) * self.scale)


def base_map(region, width, height, highlight=None):
    """Render the static map layer (sea, land, borders, highlighted countries)."""
    proj = Projection(region, width, height)
    hl = set(highlight if highlight is not None else DEFAULT_HIGHLIGHT.get(region, []))
    img = Image.new("RGB", (width, height), style.NAVY_DEEP)
    land = Image.new("L", (width, height), 0)
    lines = ImageDraw.Draw(img)
    dl = ImageDraw.Draw(land)
    lon0, lon1, lat0, lat1 = REGIONS[region]
    polys = []
    for name, rings in countries().items():
        for r in rings:
            if region != "world":
                lons = [p[0] for p in r]
                lats = [p[1] for p in r]
                if max(lons) < lon0 - 15 or min(lons) > lon1 + 15 or max(lats) < lat0 - 10 or min(lats) > lat1 + 10:
                    continue
            pts = [proj(*p) for p in r]
            if len(pts) >= 3:
                polys.append((name, pts))
                dl.polygon(pts, fill=255)
    # soft coastal glow, then land
    glow = land.filter(ImageFilter.GaussianBlur(max(2, width // 300)))
    img.paste(Image.new("RGB", img.size, style.NAVY), mask=glow.point(lambda v: v // 3))
    img.paste(Image.new("RGB", img.size, style.NAVY_LIGHT), mask=land)
    stroke = max(1, width // 1400)
    for name, pts in polys:
        if name in hl:
            lines.polygon(pts, fill=style.TERRACOTTA)
    for name, pts in polys:
        lines.line(pts + [pts[0]], fill=(70, 86, 122), width=stroke)
    return img, proj
