#!/usr/bin/env python3
"""Search renthub.in.th rooms in a zone, with the room-size filter the site lacks.

The site is a Next.js app that server-renders everything into __NEXT_DATA__, so a
zone page gives us the listings and each listing page gives us per-room
minSize/maxSize (the "33 ตร.ม." numbers) plus price and availability.

Example:
    ./search_rooms.py --min-size 30 --max-size 45 --max-price 9000 --available-only
"""

import argparse
import concurrent.futures as futures
import csv
import hashlib
import json
import os
import re
import sys
import time
import urllib.parse

import requests

BASE = "https://www.renthub.in.th"
DEFAULT_ZONE = "bts-เสนานิคม"  # BTS Sena Nikhom
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
NEXT_DATA_RE = re.compile(r'id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)

ROOM_TYPE_TH = {
    "STUDIO": "สตูดิโอ",
    "ONE_BED_ROOM": "1 ห้องนอน",
    "TWO_BED_ROOM": "2 ห้องนอน",
    "THREE_BED_ROOM": "3 ห้องนอน",
    "FOUR_BED_ROOM": "4 ห้องนอน",
    "FIVE_BED_ROOM": "5 ห้องนอน",
}


class Renthub:
    def __init__(self, cache_dir=None, delay=0.0, timeout=30):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Accept-Language": "th,en;q=0.9"})
        self.cache_dir = cache_dir
        self.delay = delay
        self.timeout = timeout
        self.build_id = None
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)

    # -- plumbing ---------------------------------------------------------
    def _cache_path(self, key):
        # Thai slugs are all non-ASCII, so a stripped name is not unique on its
        # own -- keep a readable prefix but disambiguate with a hash of the key.
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", key)[:120]
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]
        return os.path.join(self.cache_dir, f"{safe}-{digest}.json")

    def _get(self, url, key=None, max_age=None):
        """Fetch url, returning parsed JSON payload. Caches by `key`."""
        path = self._cache_path(key) if (self.cache_dir and key) else None
        if path and os.path.exists(path):
            if max_age is None or time.time() - os.path.getmtime(path) < max_age:
                with open(path, encoding="utf-8") as f:
                    return json.load(f)
        if self.delay:
            time.sleep(self.delay)
        last = None
        for attempt in range(3):
            try:
                r = self.s.get(url, timeout=self.timeout)
                if r.status_code == 404:
                    return None
                r.raise_for_status()
                data = (r.json() if "application/json" in r.headers.get("content-type", "")
                        else self._extract_next_data(r.text))
                if path:
                    with open(path, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False)
                return data
            except Exception as e:  # transient network / parse errors
                last = e
                time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"failed to fetch {url}: {last}")

    @staticmethod
    def _extract_next_data(html):
        m = NEXT_DATA_RE.search(html)
        if not m:
            raise ValueError("no __NEXT_DATA__ in page (layout changed?)")
        return json.loads(m.group(1))

    # -- pages ------------------------------------------------------------
    def zone_page(self, zone_slug, page=1, max_age=None):
        zone_path = urllib.parse.quote("อพาร์ทเม้นท์-ห้องพัก-หอพัก")
        url = f"{BASE}/{zone_path}/{urllib.parse.quote(zone_slug)}"
        if page > 1:
            url += f"?page={page}"
        data = self._get(url, key=f"zone-{zone_slug}-p{page}", max_age=max_age)
        if self.build_id is None:
            self.build_id = data.get("buildId")
        return data["props"]["pageProps"]

    def listing(self, slug, max_age=None):
        """Per-listing detail. Uses the light _next/data JSON route when possible."""
        q = urllib.parse.quote(slug)
        key = f"listing-{slug}"
        if self.build_id:
            url = f"{BASE}/_next/data/{self.build_id}/th/{q}.json?id={q}"
            data = self._get(url, key=key, max_age=max_age)
            if data is not None:
                return data["pageProps"]["listing"]
            self.build_id = None  # stale build id -> fall back to HTML
        data = self._get(f"{BASE}/{q}", key=key, max_age=max_age)
        return data["props"]["pageProps"]["listing"] if data else None


PRICE_TYPE_TH = {
    "AMOUNT": "",
    "CALL": "โทรสอบถาม",
    "NO_MONTHLY_RENTAL": "ไม่ให้เช่ารายเดือน",
    "NO_DAILY_RENTAL": "ไม่ให้เช่ารายวัน",
}


def price_range(price, kind="monthly"):
    """-> (min, max, type) for a monthly/daily price block."""
    b = (price or {}).get(kind) or {}
    return b.get("minPrice"), b.get("maxPrice"), b.get("type")


def unit_rate(fee, kind):
    """Water/electric reference rate, as a short label."""
    f = (fee or {}).get(kind) or {}
    if f.get("type") == "INCLUDED":
        return "รวม"
    for key in ("unitPrice", "perPersonPrice", "perMonthPrice"):
        if f.get(key):
            return str(f[key])
    return ""


def collect(rh, zone_slug, max_pages, workers, max_age, progress=True):
    pp = rh.zone_page(zone_slug, 1, max_age)
    pag = pp["pagination"]
    total_pages = pag["totalPages"] if max_pages is None else min(pag["totalPages"], max_pages)
    listings = list(pp["listings"])
    for p in range(2, total_pages + 1):
        listings += rh.zone_page(zone_slug, p, max_age)["listings"]
    if progress:
        print(f"zone {pp['zone']['name']}: {pag['totalCount']} listings, "
              f"fetching {len(listings)} detail pages...", file=sys.stderr)

    zone_name = pp["zone"]["name"]
    rows = []
    def work(l):
        d = rh.listing(l["slug"], max_age) or {}
        return l, d, d.get("rooms") or []

    with futures.ThreadPoolExecutor(max_workers=workers) as ex:
        for i, (l, detail, rooms) in enumerate(ex.map(work, listings), 1):
            if progress and i % 20 == 0:
                print(f"  {i}/{len(listings)}", file=sys.stderr)
            # Reference range: what the apartment advertises across all its rooms.
            # Prefer the detail page, fall back to the zone-listing card.
            rlo, rhi, rtype = price_range(detail.get("price") or l.get("price"))
            loc = detail.get("location") or {}
            water = unit_rate(detail.get("fee"), "water")
            electric = unit_rate(detail.get("fee"), "electric")
            for r in rooms:
                lo, hi, ptype = price_range(r.get("price"))
                dlo, dhi, _ = price_range(r.get("price"), "daily")
                rows.append({
                    "listing": l["name"],
                    "room": r.get("roomName") or "",
                    "type": ROOM_TYPE_TH.get(r.get("roomType"), r.get("roomType") or ""),
                    "min_size": r.get("minSize") or None,   # 0 == not published
                    "max_size": r.get("maxSize") or None,
                    "price_min": lo,
                    "price_max": hi,
                    "price_note": PRICE_TYPE_TH.get(ptype, ptype or ""),
                    "ref_price_min": rlo,
                    "ref_price_max": rhi,
                    "ref_price_note": PRICE_TYPE_TH.get(rtype, rtype or ""),
                    "water_rate": water,
                    "electric_rate": electric,
                    "daily_min": dlo,
                    "daily_max": dhi,
                    "available": bool(r.get("availability")),
                    "distance_m": l.get("distance"),
                    "district": l.get("district") or "",
                    "zones": [zone_name],
                    "nearest_zone": zone_name,
                    "lat": loc.get("lat"),
                    "lng": loc.get("lng"),
                    "url": f"{BASE}/{urllib.parse.quote(l['slug'])}",
                })
    return rows, pp["zone"]


def keep(row, a):
    size_hi, size_lo = row["max_size"], row["min_size"]
    if a.min_size is not None and (size_hi is None or size_hi < a.min_size):
        return False
    if a.max_size is not None and (size_lo is None or size_lo > a.max_size):
        return False
    if a.unknown_size_only and size_lo is not None:  # 0 already normalised to None
        return False
    lo, hi = row["price_min"], row["price_max"]
    if a.max_price is not None and (lo is None or lo > a.max_price):
        return False
    if a.min_price is not None and (hi is None or hi < a.min_price):
        return False
    if a.available_only and not row["available"]:
        return False
    if a.max_distance is not None and (row["distance_m"] is None
                                       or row["distance_m"] > a.max_distance):
        return False
    if a.room_type and row["type"] not in a.room_type:
        return False
    if a.name and a.name.lower() not in row["listing"].lower():
        return False
    return True


def fmt_range(lo, hi, unit=""):
    lo = lo or None          # the API uses 0 for "no price given"
    hi = hi or None
    if lo is None and hi is None:
        return "-"
    if lo == hi or hi is None:
        return f"{lo:,}{unit}"
    if lo is None:
        return f"{hi:,}{unit}"
    return f"{lo:,}-{hi:,}{unit}"


def width(s):
    """Display width, ignoring Thai combining marks (they stack, not advance)."""
    return sum(0 if "ั" <= c <= "ฺ" or "็" <= c <= "๎" else 1 for c in s)


def pad(s, n):
    return s + " " * max(0, n - width(s))


def print_table(rows):
    cols = [("listing", "อพาร์ทเมนท์", 34), ("room", "ห้อง", 30), ("type", "ประเภท", 11),
            ("size", "ขนาด", 9), ("price", "ราคา/เดือน", 14), ("ref", "ช่วงราคาอ้างอิง", 16),
            ("dist", "ระยะ", 7),
            ("avail", "ว่าง", 5)]
    print("  ".join(pad(h, w) for _, h, w in cols))
    print("  ".join("-" * w for *_, w in cols))
    for r in rows:
        v = {
            "listing": r["listing"], "room": r["room"], "type": r["type"],
            "size": fmt_range(r["min_size"], r["max_size"], " ตร.ม.").replace(" ตร.ม.", "sqm"),
            "price": fmt_range(r["price_min"], r["price_max"]) + (
                f" ({r['price_note']})" if r["price_note"] else ""),
            "ref": fmt_range(r["ref_price_min"], r["ref_price_max"]),
            "dist": f"{r['distance_m']}m" if r["distance_m"] is not None else "-",
            "avail": "ว่าง" if r["available"] else "เต็ม",
        }
        print("  ".join(pad(str(v[k])[:w + 6], w) for k, _, w in cols))


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--zone", action="append",
                   help=f"zone slug or full renthub zone URL, repeatable "
                        f"(default: {DEFAULT_ZONE})")
    p.add_argument("--zones-file",
                   help="file with one zone slug or URL per line (# comments ok)")
    p.add_argument("--list-zones", action="store_true",
                   help="print all BTS/MRT zone slugs and exit")
    p.add_argument("--min-size", type=float, help="ตร.ม. floor")
    p.add_argument("--max-size", type=float, help="ตร.ม. ceiling")
    p.add_argument("--min-price", type=int, help="monthly baht floor")
    p.add_argument("--max-price", type=int, help="monthly baht ceiling")
    p.add_argument("--max-distance", type=int, help="metres from the station")
    p.add_argument("--room-type", action="append",
                   help="e.g. 'สตูดิโอ' or '1 ห้องนอน' (repeatable)")
    p.add_argument("--name", help="only listings whose name contains this")
    p.add_argument("--available-only", action="store_true")
    p.add_argument("--unknown-size-only", action="store_true",
                   help="show only rooms with no size published (worth calling)")
    p.add_argument("--sort", default="size",
                   choices=["size", "price", "distance", "listing"])
    p.add_argument("--format", default="table", choices=["table", "csv", "json"])
    p.add_argument("--out", help="write to this file instead of stdout")
    p.add_argument("--max-pages", type=int, help="limit zone pages (40 listings each)")
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--cache-dir", default=os.path.expanduser("~/.cache/renthub"))
    p.add_argument("--no-cache", action="store_true")
    p.add_argument("--max-age", type=float, default=86400,
                   help="reuse cached pages younger than this many seconds")
    a = p.parse_args()

    rh = Renthub(cache_dir=None if a.no_cache else a.cache_dir)
    max_age = None if a.no_cache else a.max_age

    if a.list_zones:
        pp = rh.zone_page(DEFAULT_ZONE, 1, max_age)
        for z in sorted(pp["massTransitZones"], key=lambda z: z["name"]):
            print(f"{z['slug']}\t{z['name']}\t{z['listingCount']['monthly']} ประกาศ")
        return

    wanted = list(a.zone or [])
    if a.zones_file:
        with open(a.zones_file, encoding="utf-8") as f:
            wanted += [l.split("#")[0].strip() for l in f]
    zones = []
    for z in wanted or [DEFAULT_ZONE]:
        if not z:
            continue
        if z.startswith("http"):
            z = urllib.parse.unquote(
                urllib.parse.urlparse(z).path.rstrip("/").split("/")[-1])
        if z not in zones:                     # the same zone may be listed twice
            zones.append(z)

    rows, names, seen = [], [], {}
    for zone in zones:
        zrows, zinfo = collect(rh, zone, a.max_pages, a.workers, max_age)
        names.append(zinfo["name"])
        for r in zrows:
            # Zones overlap, so the same room shows up more than once. Keep one
            # row, but remember every zone it belongs to and the nearest station.
            k = (r["listing"], r["room"], r["min_size"], r["price_min"])
            prev = seen.get(k)
            if prev is None:
                seen[k] = r
                rows.append(r)
                continue
            for z in r["zones"]:
                if z not in prev["zones"]:
                    prev["zones"].append(z)
            if r["distance_m"] is not None and (prev["distance_m"] is None
                                                or r["distance_m"] < prev["distance_m"]):
                prev["distance_m"] = r["distance_m"]
                prev["nearest_zone"] = r["zones"][0]
    zone_label = ", ".join(names)
    hits = [r for r in rows if keep(r, a)]

    keyf = {
        "size": lambda r: (r["min_size"] is None, r["min_size"] or 0),
        "price": lambda r: (r["price_min"] is None, r["price_min"] or 0),
        "distance": lambda r: (r["distance_m"] is None, r["distance_m"] or 0),
        "listing": lambda r: r["listing"],
    }[a.sort]
    hits.sort(key=keyf)

    out = open(a.out, "w", encoding="utf-8", newline="") if a.out else sys.stdout
    try:
        if a.format == "json":
            json.dump(hits, out, ensure_ascii=False, indent=2)
            out.write("\n")
        elif a.format == "csv":
            hits = [dict(r, zones="|".join(r["zones"])) for r in hits]
            w = csv.DictWriter(out, fieldnames=list(hits[0].keys()) if hits else
                               ["listing", "room", "type", "min_size", "max_size",
                                "price_min", "price_max", "price_note",
                                "ref_price_min", "ref_price_max", "ref_price_note",
                                "water_rate", "electric_rate",
                                "daily_min", "daily_max",
                                "available", "distance_m", "district",
                                "zones", "nearest_zone", "lat", "lng", "url"])
            w.writeheader()
            w.writerows(hits)
        else:
            old = sys.stdout
            sys.stdout = out
            print_table(hits)
            sys.stdout = old
    finally:
        if a.out:
            out.close()

    print(f"\n{len(hits)} rooms matched out of {len(rows)} in {zone_label}",
          file=sys.stderr)
    if a.out:
        print(f"written to {a.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
