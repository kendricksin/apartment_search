#!/usr/bin/env python3
"""Render rooms.json (from search_rooms.py) into a self-contained map page.

    ./search_rooms.py --format json --out rooms.json
    ./make_map.py rooms.json -o map.html

The data is embedded in the HTML, so the file works when opened directly
from disk (no local server needed); only the map tiles need internet.
"""

import argparse
import json
import os
import sys

HTML = """<!DOCTYPE html>
<html lang="th">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
<style>
__LEAFLET_CSS__
</style>
<style>
  :root { --bg:#fff; --fg:#1a1a1a; --muted:#666; --line:#e3e3e3; --panel:rgba(255,255,255,.96); }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#16181c; --fg:#e9e9ea; --muted:#9a9ba0; --line:#2e3238; --panel:rgba(22,24,28,.95); }
  }
  * { box-sizing:border-box; }
  html,body { margin:0; height:100%; background:var(--bg); color:var(--fg);
    font:14px/1.45 -apple-system,"Segoe UI",Roboto,"Noto Sans Thai",sans-serif; }
  #map { position:absolute; inset:0; background:var(--bg); }
  .leaflet-container { background:var(--bg); }
  #panel { position:absolute; z-index:500; top:12px; left:12px; width:290px; max-height:calc(100% - 24px);
    overflow:auto; background:var(--panel); border:1px solid var(--line); border-radius:10px;
    padding:14px; backdrop-filter:blur(6px); box-shadow:0 4px 20px rgba(0,0,0,.14); }
  h1 { margin:0 0 2px; font-size:15px; }
  .sub { color:var(--muted); font-size:12px; margin-bottom:12px; }
  label { display:block; font-size:12px; color:var(--muted); margin:10px 0 3px; }
  .row { display:flex; gap:8px; }
  select { width:100%; padding:6px 8px; border:1px solid var(--line); border-radius:6px;
    background:var(--bg); color:var(--fg); font-size:13px; }
  input[type=number] { width:100%; padding:6px 8px; border:1px solid var(--line); border-radius:6px;
    background:var(--bg); color:var(--fg); font-size:13px; }
  .chk { display:flex; align-items:center; gap:6px; margin-top:12px; font-size:13px; color:var(--fg); }
  #count { margin-top:12px; padding-top:10px; border-top:1px solid var(--line); font-size:13px; }
  #count b { font-size:17px; }
  .legend { margin-top:10px; font-size:11px; color:var(--muted); }
  .legend span { display:inline-block; width:10px; height:10px; border-radius:50%; margin-right:4px; }
  button { margin-top:10px; width:100%; padding:7px; border:1px solid var(--line); border-radius:6px;
    background:transparent; color:var(--fg); cursor:pointer; font-size:12px; }
  .pin { border-radius:50%; border:2px solid #fff; box-shadow:0 1px 4px rgba(0,0,0,.4); }
  .pop h3 { margin:0 0 2px; font-size:14px; }
  .pop .meta { color:var(--muted); font-size:11px; margin-bottom:6px; }
  .pop table { border-collapse:collapse; font-size:12px; }
  .pop td { padding:2px 6px 2px 0; vertical-align:top; }
  .pop td.sz { white-space:nowrap; color:var(--muted); }
  .pop .full { opacity:.5; }
  .leaflet-popup-content { margin:10px 12px; max-height:240px; overflow:auto; }
</style>
</head>
<body>
<div id="map"></div>
<div id="panel">
  <h1>__TITLE__</h1>
  <div class="sub">__SUBTITLE__</div>
  <label>โซน</label>
  <select id="zone"><option value="">ทุกโซน</option>__ZONE_OPTIONS__</select>
  <label>ขนาดห้อง (ตร.ม.)</label>
  <div class="row"><input type="number" id="smin" placeholder="ต่ำสุด"><input type="number" id="smax" placeholder="สูงสุด"></div>
  <label>ราคา/เดือน (บาท)</label>
  <div class="row"><input type="number" id="pmin" placeholder="ต่ำสุด" step="500"><input type="number" id="pmax" placeholder="สูงสุด" step="500"></div>
  <label>ระยะห่างจากสถานีไม่เกิน (เมตร)</label>
  <div class="row"><input type="number" id="dmax" placeholder="เช่น 800" step="100"></div>
  <label class="chk"><input type="checkbox" id="avail" checked> ห้องว่างเท่านั้น</label>
  <label class="chk"><input type="checkbox" id="nosize"> รวมห้องที่ไม่ระบุขนาด</label>
  <div id="count"></div>
  <div class="legend">
    <div><span style="background:#2e9e5b"></span>&lt; 6,000 &nbsp;
         <span style="background:#e0a92b"></span>6,000-9,000 &nbsp;
         <span style="background:#d1503c"></span>&gt; 9,000</div>
    <div style="margin-top:4px">ขนาดวงกลม = จำนวนห้องที่ตรงเงื่อนไข</div>
  </div>
  <button id="reset">รีเซ็ตมุมมอง</button>
</div>
<script>
const DATA = __DATA__;
const CENTER = __CENTER__;
const TILES = __TILES__;

const map = L.map('map', {scrollWheelZoom:true}).setView(CENTER, __ZOOM__);

// Basemap. TILES.url may be null (markers only); TILES.dark, when present, is
// swapped in with the OS colour scheme.
let base = null;
function setBase() {
  if (!TILES.url) return;
  const dark = TILES.dark && window.matchMedia &&
               window.matchMedia('(prefers-color-scheme: dark)').matches;
  const url = dark ? TILES.dark : TILES.url;
  if (base) { base.remove(); }
  base = L.tileLayer(url, {maxZoom: TILES.maxZoom || 19, subdomains: TILES.subdomains || 'abc',
                           attribution: TILES.attribution || ''}).addTo(map);
  if (base.getContainer) base.getContainer().style.filter = TILES.filter || '';
}
setBase();
if (window.matchMedia) {
  const mq = window.matchMedia('(prefers-color-scheme: dark)');
  (mq.addEventListener ? mq.addEventListener.bind(mq, 'change') : mq.addListener.bind(mq))(setBase);
}

const layer = L.layerGroup().addTo(map);
const el = id => document.getElementById(id);
const num = id => { const v = parseFloat(el(id).value); return isNaN(v) ? null : v; };
const baht = n => n == null ? null : n.toLocaleString('en-US');

function fmt(lo, hi, suffix) {
  lo = lo || null; hi = hi || null;
  if (lo == null && hi == null) return '-';
  if (lo == null) return baht(hi) + (suffix||'');
  if (hi == null || lo === hi) return baht(lo) + (suffix||'');
  return baht(lo) + '-' + baht(hi) + (suffix||'');
}

function matches(r, f) {
  if (f.avail && !r.available) return false;
  if (!r.min_size) { if (!f.nosize) return false; }
  else {
    if (f.smin != null && (r.max_size || r.min_size) < f.smin) return false;
    if (f.smax != null && r.min_size > f.smax) return false;
  }
  if (f.pmax != null && (!r.price_min || r.price_min > f.pmax)) return false;
  if (f.pmin != null && ((r.price_max || r.price_min || 0) < f.pmin)) return false;
  if (f.dmax != null && (r.distance_m == null || r.distance_m > f.dmax)) return false;
  if (f.zone && !(r.zones || []).includes(f.zone)) return false;
  return true;
}

function colorFor(p) { return !p ? '#8a8f98' : p < 6000 ? '#2e9e5b' : p <= 9000 ? '#e0a92b' : '#d1503c'; }

function render() {
  const f = {smin:num('smin'), smax:num('smax'), pmin:num('pmin'), pmax:num('pmax'),
             dmax:num('dmax'), zone:el('zone').value,
             avail:el('avail').checked, nosize:el('nosize').checked};
  const hits = DATA.filter(r => matches(r, f));
  layer.clearLayers();

  const byPlace = new Map();
  for (const r of hits) {
    const k = r.listing + '@' + r.lat + ',' + r.lng;
    if (!byPlace.has(k)) byPlace.set(k, []);
    byPlace.get(k).push(r);
  }

  for (const rooms of byPlace.values()) {
    const a = rooms[0];
    if (a.lat == null) continue;
    const cheapest = Math.min(...rooms.map(r => r.price_min || Infinity));
    const d = 16 + Math.min(rooms.length - 1, 5) * 4;
    const marker = L.marker([a.lat, a.lng], {icon: L.divIcon({
      className:'', iconSize:[d,d], iconAnchor:[d/2,d/2],
      html:`<div class="pin" style="width:${d}px;height:${d}px;background:${colorFor(isFinite(cheapest)?cheapest:0)}"></div>`
    })});
    const rows = rooms.map(r => `<tr class="${r.available?'':'full'}">
        <td class="sz">${r.min_size ? fmt(r.min_size, r.max_size) + ' ตร.ม.' : '- ตร.ม.'}</td>
        <td>${fmt(r.price_min, r.price_max)} ฿${r.price_note ? ' ('+r.price_note+')' : ''}</td>
        <td>${r.room || r.type}</td></tr>`).join('');
    marker.bindPopup(`<div class="pop"><h3>${a.listing}</h3>
      <div class="meta">${a.district} · ${a.nearest_zone || ''} ${a.distance_m != null ? a.distance_m + ' ม.' : ''}
        · อ้างอิง ${fmt(a.ref_price_min, a.ref_price_max)} ฿/เดือน
        · น้ำ ${a.water_rate||'-'} / ไฟ ${a.electric_rate||'-'}</div>
      <table>${rows}</table>
      <div style="margin-top:6px"><a href="${a.url}" target="_blank" rel="noopener">เปิดหน้าประกาศ &rarr;</a></div></div>`,
      {maxWidth:330});
    marker.addTo(layer);
  }
  el('count').innerHTML = `<b>${hits.length}</b> ห้อง · ${byPlace.size} อพาร์ทเมนท์`;
}

for (const id of ['smin','smax','pmin','pmax','dmax'])
  el(id).addEventListener('input', render);
for (const id of ['avail','nosize','zone']) el(id).addEventListener('change', render);
L.control.scale({imperial:false}).addTo(map);
el('reset').addEventListener('click', () => map.setView(CENTER, __ZOOM__));
render();
</script>
</body>
</html>
"""


# Keyless raster basemaps. OSM's own tile server asks that apps not use it as a
# basemap (tile usage policy), so it is available but not the default.
TILE_PROVIDERS = {
    "carto": {
        "url": "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
        "dark": "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
        "attribution": "&copy; OpenStreetMap contributors &copy; CARTO",
        "subdomains": "abcd", "maxZoom": 20,
    },
    "carto-voyager": {
        "url": "https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png",
        "attribution": "&copy; OpenStreetMap contributors &copy; CARTO",
        "subdomains": "abcd", "maxZoom": 20,
    },
    "esri": {
        "url": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/"
               "MapServer/tile/{z}/{y}/{x}",
        "attribution": "Tiles &copy; Esri", "maxZoom": 19,
    },
    "esri-gray": {
        "url": "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/"
               "World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}",
        "attribution": "Tiles &copy; Esri", "maxZoom": 16,
    },
    "osm": {
        "url": "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
        "attribution": "&copy; OpenStreetMap contributors", "maxZoom": 19,
    },
    "none": {"url": None, "attribution": ""},
}

LEAFLET_CSS_URL = "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css"


def leaflet_css(cache="~/.cache/renthub/leaflet.min.css"):
    """Inline Leaflet's stylesheet so the page is one self-contained file."""
    path = os.path.expanduser(cache)
    if os.path.exists(path):
        return open(path, encoding="utf-8").read()
    import urllib.request
    req = urllib.request.Request(LEAFLET_CSS_URL, headers={"User-Agent": "Mozilla/5.0"})
    css = urllib.request.urlopen(req, timeout=30).read().decode("utf-8")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(css)
    return css


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("json_file", nargs="?", default="rooms.json")
    p.add_argument("-o", "--out", default="map.html")
    p.add_argument("--title", default="ห้องเช่า กรุงเทพ")
    p.add_argument("--tiles", default="carto", choices=sorted(TILE_PROVIDERS),
                   help="basemap provider (default: carto, keyless)")
    p.add_argument("--tiles-url",
                   help="custom {z}/{x}/{y} tile URL, e.g. a MapTiler/Stadia URL "
                        "with your own key; overrides --tiles")
    p.add_argument("--tiles-attribution", default="",
                   help="attribution text for --tiles-url")
    p.add_argument("--zoom", type=int, default=13, help="initial zoom (default 13)")
    a = p.parse_args()

    with open(a.json_file, encoding="utf-8") as f:
        rows = json.load(f)
    pts = [(r["lat"], r["lng"]) for r in rows if r.get("lat")]
    if not pts:
        sys.exit("no rows with coordinates in " + a.json_file)
    center = [sum(x) / len(pts) for x in zip(*pts)]
    places = len({r["listing"] for r in rows})
    sizes = [r["min_size"] for r in rows if r.get("min_size")]
    zones = sorted({z for r in rows for z in r.get("zones", [])})
    sub = f"{len(rows)} ห้อง · {places} อพาร์ทเมนท์"
    if sizes:
        sub += f" · ขนาด {min(sizes):g}-{max(sizes):g} ตร.ม."
    if zones:
        sub += f" · {len(zones)} โซน"

    tiles = dict(TILE_PROVIDERS[a.tiles])
    if a.tiles_url:
        tiles = {"url": a.tiles_url, "attribution": a.tiles_attribution, "maxZoom": 20}

    zone_opts = "".join(
        f'<option value="{z}">{z} '
        f'({sum(1 for r in rows if z in r.get("zones", []))})</option>'
        for z in zones)

    html = (HTML.replace("__LEAFLET_CSS__", leaflet_css())
                .replace("__ZONE_OPTIONS__", zone_opts)
                .replace("__TILES__", json.dumps(tiles, ensure_ascii=False))
                .replace("__ZOOM__", str(a.zoom))
                .replace("__DATA__", json.dumps(rows, ensure_ascii=False))
                .replace("__CENTER__", json.dumps(center))
                .replace("__TITLE__", a.title)
                .replace("__SUBTITLE__", sub))
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"wrote {a.out} ({os.path.getsize(a.out)/1024:.0f} KB, {len(rows)} rooms)")


if __name__ == "__main__":
    main()
