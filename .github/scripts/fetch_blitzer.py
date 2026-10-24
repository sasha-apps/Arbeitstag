#!/usr/bin/env python3
"""Sammelt Blitzer-Standorte für den Blitzer-Tab von ArbeitsTag und schreibt blitzer.json.

Quellen:
  * OpenStreetMap (Overpass) – feste Anlagen im Kreis Herford und in Melle (ODbL).
  * kreis-herford.de – angekündigte mobile Messstellen, tagesgenau mit Straße.
  * landkreis-osnabrueck.de – Geschwindigkeitsmessplan, nur Tag und Ort.

Straßen aus den Ankündigungen werden über OpenStreetMap in Koordinaten umgerechnet,
damit die App nach Entfernung sortieren kann. Nur Standardbibliothek.
"""
import json, re, sys, html, time, math, urllib.request, urllib.parse
from datetime import datetime, timezone, date

UA = "Mozilla/5.0 (compatible; ArbeitsTag-BlitzerBot/1.0; +https://github.com/sasha-apps)"
OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]

KH_URL = "https://www.kreis-herford.de/Startseite/Blitzer/"
LKOS_URL = "https://www.landkreis-osnabrueck.de/fachthemen/ordnung-und-verkehr/baustellen-und-blitzplan"

# Mittelpunkt: Bruchmühlen (wird, wenn möglich, aus OSM bestätigt)
CENTER = {"name": "Bruchmühlen", "lat": 52.2075, "lon": 8.4283}
NEAR_KM = 30.0  # so weit schauen wir um Bruchmühlen herum

KREIS_HF = "Kreis Herford"
LK_OS = "Landkreis Osnabrück"
GEMEINDEN = [(KREIS_HF, g) for g in
             ["Bünde", "Enger", "Herford", "Hiddenhausen", "Kirchlengern",
              "Löhne", "Rödinghausen", "Spenge", "Vlotho"]] + [(LK_OS, "Melle")]

WD = {"montag": 0, "dienstag": 1, "mittwoch": 2, "donnerstag": 3,
      "freitag": 4, "samstag": 5, "sonntag": 6}
MONATE = {"januar": 1, "februar": 2, "märz": 3, "april": 4, "mai": 5, "juni": 6,
          "juli": 7, "august": 8, "september": 9, "oktober": 10, "november": 11, "dezember": 12}


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def fetch(url, data=None, tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(
                url, data=data,
                headers={"User-Agent": UA, "Accept": "*/*",
                         "Content-Type": "application/x-www-form-urlencoded"} if data
                else {"User-Agent": UA, "Accept": "text/html,*/*"})
            with urllib.request.urlopen(req, timeout=180) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:
            last = e
            log(f"  Versuch {i+1} fehlgeschlagen: {e}")
            time.sleep(5 * (i + 1))
    raise last


def overpass(q):
    """Overpass-Abfrage, mit Ausweichserver."""
    body = urllib.parse.urlencode({"data": q}).encode()
    last = None
    for url in OVERPASS:
        try:
            txt = fetch(url, data=body)
            if not txt.lstrip().startswith("{"):
                raise ValueError("keine JSON-Antwort: " + txt[:120].replace("\n", " "))
            return json.loads(txt)
        except Exception as e:
            last = e
            log(f"  Overpass {url} fehlgeschlagen: {e}")
    raise last


def haversine(a_lat, a_lon, b_lat, b_lon):
    R = 6371.0
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp = math.radians(b_lat - a_lat)
    dl = math.radians(b_lon - a_lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def esc(s):
    return s.replace("\\", "\\\\").replace('"', '\\"')


def area_prolog(kreis, gemeinde):
    """Gemeindefläche sicher über die Kreisfläche eingrenzen."""
    return (f'area["boundary"="administrative"]["admin_level"="6"]["name"="{esc(kreis)}"]->.kreis;\n'
            f'rel["boundary"="administrative"]["admin_level"="8"]["name"="{esc(gemeinde)}"](area.kreis);\n'
            f'map_to_area->.a;\n')


def strip_tags(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


# ---------- feste Anlagen aus OpenStreetMap ----------

def fixed_cameras():
    out = []
    for kreis, gem in GEMEINDEN:
        q = ("[out:json][timeout:180];\n" + area_prolog(kreis, gem) +
             'node["highway"="speed_camera"](area.a);\nout tags center;\n')
        try:
            j = overpass(q)
        except Exception as e:
            log(f"  {gem}: feste Anlagen nicht abrufbar ({e})")
            continue
        cams = [e for e in j.get("elements", []) if e.get("type") == "node"]
        log(f"  {gem}: {len(cams)} feste Anlagen")
        for e in cams:
            t = e.get("tags", {})
            out.append({"id": f"n{e['id']}", "lat": e["lat"], "lon": e["lon"],
                        "place": gem, "maxspeed": t.get("maxspeed"),
                        "kind": "red_light" if t.get("surveillance") == "red_light" else "speed",
                        "road": None})
        time.sleep(2)
    if out:
        name_roads(out)
    return out


def name_roads(cams):
    """Straßennamen für die festen Anlagen nachladen (in Blöcken, schont den Server)."""
    for i in range(0, len(cams), 20):
        block = cams[i:i + 20]
        parts = ["[out:json][timeout:180];", "("]
        for c in block:
            parts.append(f'way(around:45,{c["lat"]},{c["lon"]})["highway"]["name"];')
        parts.append(");")
        parts.append("out tags center;")
        try:
            j = overpass("\n".join(parts))
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
        time.sleep(2)


# ---------- angekündigte Messstellen: Kreis Herford ----------

def kreis_herford():
    """<p><strong>Montag, 26.10.2026</strong></p> gefolgt von <p>• Ort, Straße</p>"""
    try:
        doc = fetch(KH_URL)
    except Exception as e:
        log(f"  Kreis Herford nicht abrufbar: {e}")
        return []
    body = re.split(r"Aktuelle\s*Kreisblitzer", doc, maxsplit=1)
    body = body[1] if len(body) > 1 else doc
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
        if e and cur:
            txt = e.group(1).strip()
            if "," in txt:
                place, road = [x.strip() for x in txt.split(",", 1)]
            else:
                place, road = None, txt
            if place:
                out.append({"date": cur, "place": place, "road": road,
                            "src": "Kreis Herford", "lat": None, "lon": None})
        elif cur is None and len(out) > 200:
            break
    log(f"  Kreis Herford: {len(out)} angekündigte Messstellen")
    return out


# ---------- angekündigte Messstellen: Landkreis Osnabrück ----------

def lk_osnabrueck():
    """'Geschwindigkeitsmessplan vom 21. Oktober bis 1. November 2026' + '21.10. Belm + 22.10. Ankum ...'"""
    try:
        doc = fetch(LKOS_URL)
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
    # Monatswechsel im Plan: Dezember-Einträge gehören ggf. ins Vorjahr, Januar ins Folgejahr
    out = []
    # Ortsname bis zum nächsten Trenner, nächsten Datum oder Textende
    for m in re.finditer(r"(\d{1,2})\.(\d{1,2})\.\s*([A-ZÄÖÜ][^+<\d]{1,30}?)"
                         r"(?=\s*(?:\+|\d{1,2}\.\d{1,2}\.|$|Hinweis))", chunk):
        dd, mm, place = int(m.group(1)), int(m.group(2)), m.group(3).strip(" .")
        y = year
        try:
            iso = date(y, mm, dd).isoformat()
        except ValueError:
            continue
        out.append({"date": iso, "place": place, "road": None,
                    "src": "Landkreis Osnabrück", "lat": None, "lon": None})
    log(f"  Landkreis Osnabrück: {len(out)} Einträge im Messplan")
    return out


# ---------- Straßen und Orte in Koordinaten umrechnen ----------

def variants(road):
    """Schreibweisen angleichen: 'Herforder Str.' / 'Industrie Straße' / 'Wiehen Straße'."""
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
        add(m.group(1) + m.group(2).lower())      # Industrie Straße -> Industriestraße
        add(m.group(1) + "-" + m.group(2))         # Industrie-Straße
    m2 = re.match(r"^(.*?)(straße|weg|allee|ring)$", base, re.I)
    if m2 and m2.group(1) and " " not in base and "-" not in base:
        add(m2.group(1) + " " + m2.group(2).capitalize())
    return v


def geocode(mobile, fixed_by_place):
    """Ergänzt lat/lon für angekündigte Messstellen."""
    todo = {}
    for e in mobile:
        if e["road"]:
            todo.setdefault(e["place"], set()).add(e["road"])
    known_kreis = {g: k for k, g in GEMEINDEN}
    coords = {}
    for place, roads in todo.items():
        kreis = known_kreis.get(place)
        if not kreis:
            log(f"  {place}: keine Gemeinde im Suchgebiet, übersprungen")
            continue
        wanted = {}
        for r in roads:
            for v in variants(r):
                wanted.setdefault(v, r)
        parts = ["[out:json][timeout:180];", area_prolog(kreis, place), "("]
        for v in wanted:
            parts.append(f'way["highway"]["name"="{esc(v)}"](area.a);')
            m = re.match(r"^([ABLK])\s?(\d{1,4})$", v)
            if m:
                parts.append(f'way["highway"]["ref"~"^{m.group(1)}\\\\s?{m.group(2)}$"](area.a);')
        parts.append(");")
        parts.append("out tags center;")
        try:
            j = overpass("\n".join(parts))
        except Exception as e:
            log(f"  {place}: Straßen nicht abrufbar ({e})")
            continue
        acc = {}
        for w in j.get("elements", []):
            c = w.get("center")
            t = w.get("tags", {})
            key = t.get("name") or t.get("ref")
            if not c or not key:
                continue
            orig = wanted.get(key) or wanted.get((t.get("ref") or "").replace(" ", ""))
            if not orig:
                continue
            acc.setdefault(orig, []).append((c["lat"], c["lon"]))
        for road, pts in acc.items():
            coords[(place, road)] = (sum(p[0] for p in pts) / len(pts),
                                     sum(p[1] for p in pts) / len(pts))
        log(f"  {place}: {len(acc)}/{len(roads)} Straßen gefunden")
        time.sleep(2)

    for e in mobile:
        if e["road"] and (e["place"], e["road"]) in coords:
            e["lat"], e["lon"] = coords[(e["place"], e["road"])]
    return mobile


def place_coords(names):
    """Ortsmittelpunkte, um weit entfernte Orte auszusortieren."""
    if not names:
        return {}
    parts = ["[out:json][timeout:120];", "("]
    for n in names:
        parts.append(f'node["place"~"^(city|town|village|suburb)$"]["name"="{esc(n)}"]'
                     f'(51.8,7.8,52.7,9.0);')
    parts.append(");")
    parts.append("out tags center;")
    try:
        j = overpass("\n".join(parts))
    except Exception as e:
        log(f"  Ortsmittelpunkte nicht abrufbar ({e})")
        return {}
    out = {}
    for e in j.get("elements", []):
        n = e.get("tags", {}).get("name")
        if n and n not in out and e.get("lat"):
            out[n] = (e["lat"], e["lon"])
    return out


def confirm_center():
    try:
        j = overpass('[out:json][timeout:60];node["place"]["name"="Bruchmühlen"]'
                     '(52.0,8.2,52.4,8.7);out center 1;')
        for e in j.get("elements", []):
            if e.get("lat"):
                CENTER["lat"], CENTER["lon"] = e["lat"], e["lon"]
                log(f"  Mittelpunkt aus OSM: {CENTER['lat']:.4f}, {CENTER['lon']:.4f}")
                return
    except Exception as e:
        log(f"  Mittelpunkt nicht bestätigt, nehme Standardwert ({e})")


def main(path):
    try:
        with open(path, encoding="utf-8") as f:
            old = json.load(f)
    except Exception:
        old = {}

    log("Mittelpunkt prüfen …")
    confirm_center()

    log("Feste Anlagen aus OpenStreetMap …")
    fixed = fixed_cameras()
    if not fixed and old.get("fixed"):
        log("  nichts bekommen, behalte den alten Stand")
        fixed = old["fixed"]

    log("Angekündigte Messstellen …")
    mobile = kreis_herford() + lk_osnabrueck()

    # Orte außerhalb des Umkreises aussortieren (betrifft vor allem den Landkreis Osnabrück)
    unknown = sorted({e["place"] for e in mobile if e["place"] not in {g for _, g in GEMEINDEN}})
    pc = place_coords(unknown) if unknown else {}
    keep = []
    for e in mobile:
        if e["place"] in {g for _, g in GEMEINDEN}:
            keep.append(e)
        elif e["place"] in pc:
            lat, lon = pc[e["place"]]
            if haversine(CENTER["lat"], CENTER["lon"], lat, lon) <= NEAR_KM:
                e["lat"], e["lon"] = lat, lon
                keep.append(e)
    mobile = keep

    log("Straßen in Koordinaten umrechnen …")
    mobile = geocode(mobile, {})

    today = date.today().isoformat()
    mobile = [e for e in mobile if e["date"] >= today]
    mobile.sort(key=lambda e: (e["date"], e["place"], e["road"] or ""))

    for c in fixed:
        c["km"] = round(haversine(CENTER["lat"], CENTER["lon"], c["lat"], c["lon"]), 2)
    fixed.sort(key=lambda c: c["km"])

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
    if not fixed and not mobile:
        log("Keine Daten – blitzer.json bleibt unverändert.")
        return 1
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        f.write("\n")
    log(f"Fertig: {len(fixed)} feste Anlagen, {len(mobile)} angekündigte Messstellen.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "blitzer.json"))
