#!/usr/bin/env python3
"""Holt Nachrichten-Feeds und schreibt news.json für den Welt-Tab von ArbeitsTag.
Läuft stündlich über GitHub Actions. Nur Standardbibliothek, keine Abhängigkeiten.
Gespeichert werden nur Schlagzeile, kurzer Anriss, Quelle, Zeit und Link zum Originalartikel."""
import json, re, sys, html, time, urllib.request, urllib.parse, hashlib
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone, timedelta

UA = "Mozilla/5.0 (compatible; ArbeitsTag-NewsBot/1.0; +https://github.com/sasha-apps)"
NOW = datetime.now(timezone.utc)
MAX_AGE_H = {"welt": 36, "de": 36, "region": 96}

def gnews(q, lang="de"):
    if lang == "de":
        return "https://news.google.com/rss/search?" + urllib.parse.urlencode({"q": q, "hl": "de", "gl": "DE", "ceid": "DE:de"})
    return "https://news.google.com/rss/search?" + urllib.parse.urlencode({"q": q, "hl": "en-US", "gl": "US", "ceid": "US:en"})

# (Kategorie, Quelle, URL, Ort)
FEEDS = [
    ("welt", "tagesschau", "https://www.tagesschau.de/ausland/index~rss2.xml", None),
    ("de", "tagesschau", "https://www.tagesschau.de/inland/index~rss2.xml", None),
    ("welt", "DW", "https://rss.dw.com/rdf/rss-de-top", None),
    ("de", "ZDFheute", "https://www.zdfheute.de/rss/zdf/nachrichten", None),
    ("de", "SPIEGEL", "https://www.spiegel.de/schlagzeilen/tops/index.rss", None),
    ("de", "ZEIT", "https://newsfeed.zeit.de/index", None),
    ("welt", "BBC", "https://feeds.bbci.co.uk/news/world/rss.xml", None),
    ("welt", "The Guardian", "https://www.theguardian.com/world/rss", None),
    ("welt", "Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml", None),
    # Regional: Google-News-Suche je Ort (bündelt NW, NOZ, Westfalen-Blatt, Radio Herford, WDR, NDR …)
    ("region", None, gnews('"Herford" when:3d'), "Herford"),
    ("region", None, gnews('Melle when:4d'), "Melle"),
    ("region", None, gnews('"Bielefeld" when:2d'), "Bielefeld"),
    ("region", None, gnews('"Osnabrück" when:2d'), "Osnabrück"),
]
OP_FEEDS = [gnews('"One Piece" Kapitel when:21d'), gnews('"One Piece" chapter when:21d', "en")]
MANGAPLUS = "https://mangaplus.shueisha.co.jp/titles/100020"
SKIP_RX = r"^(Entdecken Sie|Aktuelle Pressemitteilungen|Newsletter|Podcast:)|\((?:Mo|Di|Mi|Do|Fr|Sa|So)\.,\s*\d{1,2}\.\d{1,2}\.\d{4}"
PLACE_RX = {"Herford": r"herford", "Melle": r"\bmelle\b|meller", "Bielefeld": r"bielefeld", "Osnabrück": r"osnabr[üu]c?k"}
UPCOMING = r"spoiler|leak|predict|preview|vorschau|raws?\b|scans?\b|erscheint|release date|release time|when does|wann|countdown|delay|break|pause|verschoben|next week|nächste woche|will be"

def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read()

def strip(s, n=None):
    s = html.unescape(re.sub(r"<[^>]+>", " ", s or ""))
    s = re.sub(r"\s+", " ", s).strip()
    if n and len(s) > n:
        s = s[: n - 1].rsplit(" ", 1)[0] + "…"
    return s

def local(tag):
    return tag.rsplit("}", 1)[-1].lower()

def child(el, *names):
    for c in el:
        if local(c.tag) in names:
            return c
    return None

def text(el, *names):
    c = child(el, *names)
    return (c.text or "").strip() if c is not None and c.text else ""

def parse_date(s):
    if not s:
        return None
    try:
        d = parsedate_to_datetime(s)
    except Exception:
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d

def parse_feed(raw):
    """RSS 2.0, RDF/RSS 1.0 und Atom."""
    root = ET.fromstring(raw)
    items = [e for e in root.iter() if local(e.tag) in ("item", "entry")]
    out = []
    for it in items:
        title = strip(text(it, "title"))
        link = text(it, "link")
        if not link:
            l = child(it, "link")
            if l is not None:
                link = l.get("href", "")
        summary = text(it, "description", "summary", "content")
        date = parse_date(text(it, "pubdate", "date", "updated", "published"))
        src = child(it, "source")
        out.append({"title": title, "link": link.strip(), "summary": summary, "date": date,
                    "source": (src.text or "").strip() if src is not None and src.text else ""})
    return out

def main(out_path):
    items, status, seen = [], [], set()
    for cat, name, url, place in FEEDS:
        try:
            entries = parse_feed(fetch(url))
            n = 0
            for e in entries:
                if not e["title"] or not e["link"].startswith("http"):
                    continue
                if not e["date"]:  # ohne Datum meist keine Nachricht (z. B. Service-Seiten)
                    continue
                d = e["date"]
                if NOW - d > timedelta(hours=MAX_AGE_H[cat]) or d - NOW > timedelta(hours=2):
                    continue
                title, src = e["title"], name
                if re.search(SKIP_RX, title):  # Service-Seiten und Veranstaltungskalender
                    continue
                if name is None:  # Google News: "Titel - Quelle"
                    m = re.match(r"^(.*)\s+-\s+(.{2,60})$", title)
                    if m:
                        title, src = m.group(1).strip(), m.group(2).strip()
                    src = e["source"] or src or "Google News"
                if place and (len(title) < 20 or not re.search(PLACE_RX[place], title, re.I)):  # Regionales muss den Ort nennen
                    continue
                key = re.sub(r"\W+", "", title.lower())[:80]
                if key in seen:
                    continue
                seen.add(key)
                summ = strip(e["summary"], 240)
                if name is None or summ.lower().startswith(title.lower()[:40]):
                    summ = ""
                items.append({"id": hashlib.sha1((e["link"] + title).encode()).hexdigest()[:12], "cat": cat, "src": src,
                              "place": place, "title": title, "summary": summ, "link": e["link"], "ts": int(d.timestamp())})
                n += 1
            status.append({"feed": place or name, "cat": cat, "ok": True, "items": n})
        except Exception as ex:
            status.append({"feed": place or name, "cat": cat, "ok": False, "error": type(ex).__name__ + ": " + str(ex)[:120]})
        time.sleep(0.5)

    # pro Kategorie bzw. Ort begrenzen
    items.sort(key=lambda x: -x["ts"])
    caps, kept = {}, []
    for it in items:
        k = it["place"] or it["cat"]
        lim = 15 if it["cat"] == "region" else 40
        sk = "src:" + it["cat"] + it["src"]
        if caps.get(k, 0) < lim and (it["cat"] == "region" or caps.get(sk, 0) < 10):
            caps[k] = caps.get(k, 0) + 1
            caps[sk] = caps.get(sk, 0) + 1
            kept.append(it)

    # One Piece: neuestes Kapitel aus Schlagzeilen ableiten
    op = {"link": MANGAPLUS}
    try:
        found = []
        for url in OP_FEEDS:
            try:
                for e in parse_feed(fetch(url)):
                    t = e["title"]
                    if "one piece" not in t.lower():
                        continue
                    for num in re.findall(r"(?:kapitel|chapter|ch\.)\s*#?(\d{4})", t, re.I):
                        upcoming = bool(re.search(UPCOMING, t, re.I))
                        found.append((int(num), upcoming, e["date"] or NOW, t, e["link"]))
            except Exception as ex:
                status.append({"feed": "One Piece", "cat": "op", "ok": False, "error": type(ex).__name__})
        released = [f for f in found if not f[1]]
        if released:
            cnt = {}
            for f in released:
                cnt[f[0]] = cnt.get(f[0], 0) + 1
            solid = [f for f in released if cnt[f[0]] >= 2] or released
            best = max(solid, key=lambda f: (f[0], -f[2].timestamp()))
            first_seen = min(f[2] for f in released if f[0] == best[0])
            op.update({"chapter": best[0], "seen": int(first_seen.timestamp()), "headline": strip(re.sub(r"\s+-\s+[^\n]{2,60}$", "", best[3]), 140), "news": best[4]})
        upcoming = [f[0] for f in found if f[1] and (not released or f[0] > op.get("chapter", 0))]
        if upcoming:
            op["next"] = min(upcoming)
    except Exception as ex:
        status.append({"feed": "One Piece", "cat": "op", "ok": False, "error": type(ex).__name__})

    op["debug"] = sorted({(f[0], f[1], f[3][:90]) for f in found}, reverse=True)[:12] if 'found' in dir() else []
    data = {"updated": int(NOW.timestamp()), "items": kept, "onepiece": op, "status": status}
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    ok = sum(1 for s in status if s["ok"])
    print(f"{len(kept)} Meldungen, {ok}/{len(status)} Quellen ok, One Piece: {op.get('chapter')}")
    for s in status:
        print(("OK  " if s["ok"] else "ERR ") + json.dumps(s, ensure_ascii=False))

if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "news.json")
