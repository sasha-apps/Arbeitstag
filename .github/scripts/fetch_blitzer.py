#!/usr/bin/env python3
"""Sammelt Blitzer-Standorte für den Blitzer-Tab von ArbeitsTag und schreibt blitzer.json.

Quellen:
  * OpenStreetMap (Overpass) – feste Anlagen im Kreis Herford und in Melle (ODbL).
  * kreis-herford.de – angekündigte mobile Messstellen, tagesgenau mit Straße.
  * landkreis-osnabrueck.de – Geschwindigkeitsmessplan, nur Tag und Ort.

Straßen aus den Ankündigungen werden über OpenStreetMap in Koordinaten umgerechnet,
damit die App nach Entfernung sortieren kann. Wenige große Abfragen statt vieler
kleiner, damit der Lauf schnell bleibt. Nur Standardbibliothek.
"""
import json, re, sys, html, time, math, urllib.request, urllib.parse
from datetime import datetime, timezone, date

UA = "Mozilla/5.0 (compatible; ArbeitsTag-BlitzerBot/1.0; +https://github.com/sasha-apps)"
OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]

KH_URL = "https://www.kreis-herford.de/Startseite/Blitzer/"
LKOS_URL = "https://www.landkreis-osnabrueck.de/fachthemen/ordnung-und-verkehr/baustellen-und-blitzplan"

CENTER = {"name": "Bruchmühlen", "lat": 52.2075, "lon": 8.4283}
NEAR_KM = 30.0            # so weit schauen wir um Bruchmühlen herum
BBOX = (52.00, 8.10, 52.50, 9.00)   # Kreis Herford, Melle und Umgebung
MAX_PLACE_KM = 7.0        # so weit darf eine Straße vom Ortsmittelpunkt entfernt sein


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def fetch(url, data=None, tries=2, timeout=90):
    last = None
    for i in range(tries):
        try:
            hdr = {"User-Agent": UA, "Accept": "*/*"}
            if data:
                hdr["Content-Type"] = "application/x-www-form-urlencoded"
            req = urllib.request.Request(url, data=data, headers=hdr)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:
            last = e
            log(f"  Versuch {i+1} fehlgeschlagen: {e}")
            if i + 1 < tries:
                time.sleep(6)
    raise last


def overpass(q, timeout=90):
    body = urllib.parse.urlencode({"data": q}).encode()
    last = None
    for url in OVERPASS:
        try:
            txt = fetch(url, data=body, timeout=timeout)
            if not txt.lstrip().startswith("{"):
                raise ValueError("keine JSON-Antwort: " + txt[:120].replace("\n", " "))
            return json.loads(txt)
        except Exception as e:
            last = e
            log(f"  Overpass {url} fehlgeschlagen: {e}")
    raise last


def haversine(a_lat, a_lon, b_lat, b_lon):
    R = 6371.0
    dp = math.radians(b_lat - a_lat)
    dl = math.radians(b_lon - a_lon)
    h = (math.sin(dp / 2) ** 2 +
         math.cos(math.radians(a_lat)) * math.cos(math.radians(b_lat)) * math.sin(dl / 2) ** 2)
    return 2 * R * math.asin(min(1.0, math.sqrt(h)))


def esc(s):
    return s.replace("\\", "\\\\").replace('"', '\\"')


def strip_tags(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


# Melle über den Landkreis eingrenzen, damit kein gleichnamiger Ort anderswo mitkommt
AREAS = ('area["boundary"="administrative"]["admin_level"="6"]["name"="Kreis Herford"]->.hf;\n'
         'area["boundary"="administrative"]["admin_level"="6"]["name"="Landkreis Osnabrück"]->.lkos;\n'
         'rel["boundary"="administrative"]["admin_level"="8"]["name"="Melle"](area.lkos);\n'
         'map_to_area->.me;\n')


# ---------- Orte (für Ortsnamen und zum Einordnen der Straßen) ----------

def all_places():
    s, w, n, e = BBOX
    q = (f'[out:json][timeout:90];\n'
         f'node["place"~"^(city|town|village)$"]["name"]({s},{w},{n},{e});\nout tags center;\n')
    try:
        j = overpass(q)
    except Exception as ex:
        log(f"  Orte nicht abrufbar ({ex})")
        return []
    out = [{"name": el["tags"]["name"], "lat": el["lat"], "lon": el["lon"]}
           for el in j.get("elements", []) if el.get("lat") and el.get("tags", {}).get("name")]
    log(f"  {len(out)} Orte in der Region")
    return out


def nearest_place(places, lat, lon):
    best, bestd = None, 9e9
    for p in places:
        d = haversine(lat, lon, p["lat"], p["lon"])
        if d < bestd:
            best, bestd = p, d
    return best["name"] if best else None


# ---------- feste Anlagen ----------

def fixed_cameras(places):
    q = ('[out:json][timeout:120];\n' + AREAS +
         '(node["highway"="speed_camera"](area.hf);node["highway"="speed_camera"](area.me););\n'
         'out tags center;\n')
    try:
        j = overpass(q, timeout=120)
    except Exception as e:
        log(f"  Feste Anlagen nicht abrufbar ({e})")
        return []
    cams = []
    for el in j.get("elements", []):
        if el.get("type") != "node" or el.get("lat") is None:
            continue
        t = el.get("tags", {})
        cams.append({"id": f"n{el['id']}", "lat": el["lat"], "lon": el["lon"],
                     "place": nearest_place(places, el["lat"], el["lon"]),
                     "maxspeed": t.get("maxspeed"),
                     "kind": "red_light" if t.get("surveillance") == "red_light" else "speed",
                     "road": None})
    log(f"  {len(cams)} feste Anlagen")
    if cams:
        name_roads(cams)
    return cams


def name_roads(cams):
    """Straßennamen in wenigen Sammelabfragen nachladen."""
    for i in range(0, len(cams), 40):
        block = cams[i:i + 40]
        parts = ["[out:json][timeout:120];", "("]
        for c in block:
            parts.append(f'way(around:45,{c["lat"]},{c["lon"]})["highway"]["name"];')
        parts += [");", "out tags center;"]
        try:
            j = overpass("\n".join(parts), timeout=120)
        except Exception as e:
            log(f"  Straßennamen nicht abrufbar ({e})")
            return
        ways = [w for w in j.get("elements", []) if w.get("center") and w.get("tags", {}).get("name")]
        for c in block:
            best, bestd = None, 9e9
            for w in ways:
                d = haversine(c["lat"], c["lon"], w["center"]["lat"], w["center"]["lon"])
                if d < bestd:
                    best, bestd = w, d
            if best and bestd < 1.0:
                c["road"] = best["tags"]["name"]
        time.sleep(1)
    log(f"  {sum(1 for c in cams if c['road'])} davon mit Straßennamen")


# ---------- angekündigte Messstellen ----------

def kreis_herford():
    """<p><strong>Montag, 26.10.2026</strong></p> gefolgt von <p>• Ort, Straße</p>"""
    try:
        doc = fetch(KH_URL, timeout=60)
    except Exception as e:
        log(f"  Kreis Herford nicht abrufbar: {e}")
        return []
    parts = re.split(r"Aktuelle\s*Kreisblitzer", doc, maxsplit=1)
    body = parts[1] if len(parts) > 1 else doc
    out, cur = [], None
    for m in re.finditer(r"<p[^>]*>(.*?)</p>", body, re.S | re.I):
        line = strip_tags(m.group(1))
        if not line:
            continue
        d = re.match(r"^([A-Za-zÄÖÜäöü]+),?\s*(\d{1,2})\.(\d{1,2})\.(\d{4})", line)
        if d:
            try:
                cur = date(int(d.group(4)), int(d.group(3)), int(d.group(2))).isoformat()
            except ValueError:
                cur = None
            continue
        e = re.match(r"^[•·\-\*]\s*(.+)$", line)
        if e and cur and "," in e.group(1):
            place, road = [x.strip() for x in e.group(1).split(",", 1)]
            if place and road:
                out.append({"date": cur, "place": place, "road": road,
                            "src": "Kreis Herford", "lat": None, "lon": None})
    log(f"  Kreis Herford: {len(out)} angekündigte Messstellen")
    return out


def lk_osnabrueck():
    """'Geschwindigkeitsmessplan vom …' + '21.10. Belm + 22.10. Ankum + …'"""
    try:
        doc = fetch(LKOS_URL, timeout=60)
    except Exception as e:
        log(f"  Landkreis Osnabrück nicht abrufbar: {e}")
        return []
    i = doc.find("Geschwindigkeitsmessplan")
    if i < 0:
        log("  Landkreis Osnabrück: kein Messplan gefunden")
        return []
    chunk = strip_tags(doc[i:i + 4000])
    years = re.findall(r"\b(20\d{2})\b", chunk[:200])
    year = int(years[0]) if years else datetime.now().year
    out = []
    for m in re.finditer(r"(\d{1,2})\.(\d{1,2})\.\s*([A-ZÄÖÜ][^+<\d]{1,30}?)"
                         r"(?=\s*(?:\+|\d{1,2}\.\d{1,2}\.|$|Hinweis))", chunk):
        dd, mm, place = int(m.group(1)), int(m.group(2)), m.group(3).strip(" .")
        try:
            iso = date(year, mm, dd).isoformat()
        except ValueError:
            continue
        out.append({"date": iso, "place": place, "road": None,
                    "src": "Landkreis Osnabrück", "lat": None, "lon": None})
    log(f"  Landkreis Osnabrück: {len(out)} Einträge im Messplan")
    return out


# ---------- Straßen in Koordinaten umrechnen ----------

def variants(road):
    """Schreibweisen angleichen: 'Herforder Str.' / 'Industrie Straße'."""
    v, seen = [], set()

    def add(x):
        x = re.sub(r"\s+", " ", x).strip()
        if x and x not in seen:
            seen.add(x)
            v.append(x)

    add(road)
    base = re.sub(r"\bStr\.?$", "Straße", road).strip()
    add(base)
    m = re.match(r"^(.*\S)\s+(Straße|Weg|Allee|Ring)$", base)
    if m:
        add(m.group(1) + m.group(2).lower())
        add(m.group(1) + "-" + m.group(2))
    m2 = re.match(r"^(.*?)(straße|weg|allee|ring)$", base, re.I)
    if m2 and m2.group(1) and " " not in base and "-" not in base:
        add(m2.group(1) + " " + m2.group(2).capitalize())
    return v


def geocode(mobile, places):
    """Eine Sammelabfrage für alle gesuchten Straßen, Zuordnung über die Ortsnähe."""
    wanted = {}          # Schreibvariante -> Originalname
    for e in mobile:
        if e["road"]:
            for v in variants(e["road"]):
                wanted.setdefault(v, e["road"])
    if not wanted:
        return mobile
    s, w, n, ea = BBOX
    parts = ["[out:json][timeout:120];", "("]
    for v in wanted:
        parts.append(f'way["highway"]["name"="{esc(v)}"]({s},{w},{n},{ea});')
        m = re.match(r"^([ABLK])\s?(\d{1,4})$", v)
        if m:
            parts.append(f'way["highway"]["ref"~"^{m.group(1)}\\\\s?{m.group(2)}$"]({s},{w},{n},{ea});')
    parts += [");", "out tags center;"]
    try:
        j = overpass("\n".join(parts), timeout=120)
    except Exception as e:
        log(f"  Straßen nicht abrufbar ({e})")
        return mobile

    by_name = {}
    for el in j.get("elements", []):
        c, t = el.get("center"), el.get("tags", {})
        key = t.get("name") or (t.get("ref") or "").replace(" ", "")
        orig = wanted.get(key)
        if c and orig:
            by_name.setdefault(orig, []).append((c["lat"], c["lon"]))
    log(f"  {len(by_name)} von {len({v for v in wanted.values()})} Straßennamen in OSM gefunden")

    pl = {p["name"]: p for p in places}
    hit = 0
    for e in mobile:
        if not e["road"] or e["road"] not in by_name:
            continue
        anchor = pl.get(e["place"])
        if not anchor:
            continue
        near = [(haversine(anchor["lat"], anchor["lon"], la, lo), la, lo)
                for la, lo in by_name[e["road"]]]
        near = [x for x in near if x[0] <= MAX_PLACE_KM]
        if not near:
            continue
        near.sort()
        best = near[:max(1, len(near) // 2)]          # die dem Ort nächsten Abschnitte mitteln
        e["lat"] = sum(x[1] for x in best) / len(best)
        e["lon"] = sum(x[2] for x in best) / len(best)
        hit += 1
    log(f"  {hit} von {sum(1 for e in mobile if e['road'])} Messstellen verortet")
    return mobile


def confirm_center(places):
    for p in places:
        if p["name"] == "Bruchmühlen":
            CENTER["lat"], CENTER["lon"] = p["lat"], p["lon"]
            log(f"  Mittelpunkt aus OSM: {p['lat']:.4f}, {p['lon']:.4f}")
            return
    try:
        j = overpass('[out:json][timeout:60];node["place"]["name"="Bruchmühlen"]'
                     '(52.0,8.2,52.4,8.7);out center 1;', timeout=60)
        for el in j.get("elements", []):
            if el.get("lat"):
                CENTER["lat"], CENTER["lon"] = el["lat"], el["lon"]
                log(f"  Mittelpunkt aus OSM: {el['lat']:.4f}, {el['lon']:.4f}")
                return
    except Exception as e:
        log(f"  Mittelpunkt nicht bestätigt, nehme Standardwert ({e})")


def main(path):
    t0 = time.time()
    try:
        with open(path, encoding="utf-8") as f:
            old = json.load(f)
    except Exception:
        old = {}

    log("Orte und Mittelpunkt …")
    places = all_places()
    confirm_center(places)

    log("Feste Anlagen aus OpenStreetMap …")
    fixed = fixed_cameras(places)
    if not fixed and old.get("fixed"):
        log("  nichts bekommen, behalte den alten Stand")
        fixed = old["fixed"]

    log("Angekündigte Messstellen …")
    mobile = kreis_herford() + lk_osnabrueck()

    # Orte außerhalb des Umkreises aussortieren (betrifft vor allem den Landkreis Osnabrück)
    pl = {p["name"]: p for p in places}
    keep = []
    for e in mobile:
        p = pl.get(e["place"])
        if not p:
            if e["src"] == "Kreis Herford":
                keep.append(e)      # Ort unbekannt, Eintrag bleibt ohne Entfernung
            continue
        if haversine(CENTER["lat"], CENTER["lon"], p["lat"], p["lon"]) <= NEAR_KM:
            e["lat"], e["lon"] = p["lat"], p["lon"]
            keep.append(e)
    mobile = keep

    log("Straßen in Koordinaten umrechnen …")
    mobile = geocode(mobile, places)

    today = date.today().isoformat()
    mobile = [e for e in mobile if e["date"] >= today]
    mobile.sort(key=lambda e: (e["date"], e["place"], e["road"] or ""))

    for c in fixed:
        c["km"] = round(haversine(CENTER["lat"], CENTER["lon"], c["lat"], c["lon"]), 2)
    fixed.sort(key=lambda c: c["km"])

    if not fixed and not mobile:
        log("Keine Daten – blitzer.json bleibt unverändert.")
        return 1

    data = {
        "updated": int(datetime.now(timezone.utc).timestamp()),
        "center": CENTER,
        "fixed": fixed,
        "mobile": mobile,
        "sources": [
            {"name": "OpenStreetMap", "url": "https://www.openstreetmap.org/copyright",
             "note": "Feste Anlagen, © OpenStreetMap-Mitwirkende (ODbL)"},
            {"name": "Kreis Herford", "url": KH_URL,
             "note": "Angekündigte Messstellen des Kreises Herford"},
            {"name": "Landkreis Osnabrück", "url": LKOS_URL,
             "note": "Geschwindigkeitsmessplan, nur Tag und Ort"},
        ],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        f.write("\n")
    log(f"Fertig in {time.time()-t0:.0f}s: {len(fixed)} feste Anlagen, "
        f"{len(mobile)} angekündigte Messstellen.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "blitzer.json"))
