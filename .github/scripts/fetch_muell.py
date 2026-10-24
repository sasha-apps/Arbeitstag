#!/usr/bin/env python3
"""Holt den Abfuhrkalender der Gemeinde Rödinghausen für alle Straßen und schreibt muell.json.

Die Gemeinde bietet je Straße einen iCal-Export an. Viele Straßen teilen sich denselben
Plan, deshalb speichern wir jeden Plan nur einmal und merken uns je Straße die Nummer.
Die App wählt die eigene Straße erst auf dem Gerät aus, die Datei enthält keine Adresse.
Nur Standardbibliothek.
"""
import html, json, re, sys, time, urllib.request
from datetime import date

BASE = "https://www.roedinghausen.de"
PAGE = BASE + "/index.php?La=1&ffmod=abf&ort=393.1&call=0&set=fix"
ICS = BASE + "/output/abfall_export.php?csv_export=1&mode=vcal&ort=393.1&strasse={s}&vtyp=4&vMo={m}&vJ={y}&bMo=12"
UA = "ArbeitsTag/1.0 (+https://github.com/sasha-apps/Arbeitstag)"


def fetch(url, tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=40) as r:
                raw = r.read()
                m = re.search(r"charset=([\w-]+)", r.headers.get("Content-Type") or "")
                cs = m.group(1) if m else None
            if not cs:
                m = re.search(rb'<meta[^>]+charset="?([\w-]+)', raw[:4000], re.I)
                cs = m.group(1).decode() if m else "utf-8"
            return raw.decode(cs, "replace")
        except Exception as e:
            last = e
            time.sleep(4)
    raise last


def streets():
    page = fetch(PAGE)
    sel = re.search(r'<select name="strasse".*?</select>', page, re.S)
    if not sel:
        raise RuntimeError("Straßenliste nicht gefunden")
    return [(v, html.unescape(n).strip()) for v, n in re.findall(r'<option value="([\d.]+)">([^<]+)', sel.group(0))]


def kind(summary):
    """„RH Graue Tonne (Restmüll): Rödinghausen“ → „Graue Tonne (Restmüll)“."""
    s = re.sub(r"^\s*RH\s+", "", summary)
    return re.sub(r"\s*:\s*[^:]*$", "", s).strip()


def events(street, year, month):
    txt = fetch(ICS.format(s=street, y=year, m=month))
    out = []
    for ev in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", txt, re.S):
        d = re.search(r"DTSTART;VALUE=DATE:(\d{8})", ev)
        s = re.search(r"SUMMARY:(.*)", ev)
        if d and s:
            v = d.group(1)
            out.append((kind(s.group(1).strip()), f"{v[:4]}-{v[4:6]}-{v[6:]}"))
    return out


def main(path):
    today = date.today()
    years = [(today.year, 1)] + ([(today.year + 1, 1)] if today.month >= 10 else [])
    try:
        sts = streets()
    except Exception as e:
        print("Straßenliste nicht abrufbar:", e)
        return
    print(f"{len(sts)} Straßen")
    types, plans, sig, rows, fails = [], [], {}, [], 0
    for sid, name in sts:
        evs = []
        try:
            for y, m in years:
                evs += events(sid, y, m)
        except Exception as e:
            fails += 1
            print(f"  {name}: {e}")
            continue
        plan = {}
        for k, d in evs:
            if k not in types:
                types.append(k)
            plan.setdefault(types.index(k), set()).add(d)
        plan = {str(k): sorted(v) for k, v in sorted(plan.items())}
        key = json.dumps(plan, sort_keys=True)
        if key not in sig:
            sig[key] = len(plans)
            plans.append(plan)
        rows.append([name, sig[key]])
        time.sleep(0.3)
    if not rows or fails > len(sts) // 4:
        print(f"Zu viele Fehler ({fails}), alte Datei bleibt.")
        return
    try:
        old = json.load(open(path, encoding="utf-8"))
        if {k: old.get(k) for k in ("types", "plans", "streets")} == {"types": types, "plans": plans, "streets": rows}:
            print("Keine Änderungen.")
            return
    except Exception:
        pass
    json.dump({"updated": int(time.time()), "src": "Gemeinde Rödinghausen", "types": types, "plans": plans, "streets": rows},
              open(path, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print(f"{len(rows)} Straßen, {len(plans)} Pläne, {len(types)} Abfallarten")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "muell.json")
