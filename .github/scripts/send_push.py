#!/usr/bin/env python3
"""Verschickt geplante ArbeitsTag-Benachrichtigungen per Web Push.

Liest push.json aus dem privaten Daten-Repo (Token in DATA_TOKEN). Die Texte der
Erinnerungen sind auf dem Gerät mit dem Passwort-Schlüssel verschlüsselt; dieses
Skript sieht nur Zeitpunkte und den verschlossenen Inhalt. Für die Morgen- und
Feierabend-Meldung hängt es Verkehrsmeldungen der Autobahn GmbH an, morgens dazu das
Wetter (Open-Meteo). Am Vorabend eines Arbeitstags warnt es bei Glätte, Sturm oder Nebel
(Open-Meteo und DWD-Warnungen über Bright Sky).
Gibt bewusst nichts Persönliches im Log aus (das Repo ist öffentlich).
"""
import base64, hashlib, hmac, json, os, re, sys, time, urllib.error, urllib.request
from datetime import datetime, timedelta
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

API = os.environ.get("GITHUB_API", "https://api.github.com")
AUTOBAHN = os.environ.get("AUTOBAHN_API", "https://verkehr.autobahn.de/o/autobahn")
REPO = os.environ.get("DATA_REPO", "sasha-apps/arbeitstag-daten")
TOKEN = os.environ.get("DATA_TOKEN", "").strip()
WINDOW = 2 * 3600          # später als 2 Std. wird nichts mehr verschickt
KEEP_IDS = 3 * 86400       # gesendete IDs so lange merken
BERLIN = ZoneInfo("Europe/Berlin")
HOME = (52.2003, 8.4554)   # Bruchmühlen; mit Arbeitsweg der Startpunkt der Strecke
WARN_HOURS = (19, 22)      # Warnung am Vorabend zwischen 19 und 22 Uhr
UA = "ArbeitsTag-Push/1.0 (+https://sasha-apps.github.io/Arbeitstag/)"


def b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def ub64(s):
    s = s.strip().replace("+", "-").replace("/", "_")
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _hmac(key, data):
    return hmac.new(key, data, hashlib.sha256).digest()


def _hkdf(salt, ikm, info, n):
    return _hmac(_hmac(salt, ikm), info + b"\x01")[:n]


def encrypt(payload, ua_pub, auth, as_priv=None, salt=None):
    """RFC 8291 (aes128gcm), ein Datensatz."""
    as_priv = as_priv or ec.generate_private_key(ec.SECP256R1())
    as_pub = as_priv.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    ecdh = as_priv.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_pub))
    ikm = _hkdf(auth, ecdh, b"WebPush: info\x00" + ua_pub + as_pub, 32)
    salt = salt or os.urandom(16)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    ct = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    return salt + (4096).to_bytes(4, "big") + bytes([len(as_pub)]) + as_pub + ct


def vapid_header(endpoint, d_b64):
    key = ec.derive_private_key(int.from_bytes(ub64(d_b64), "big"), ec.SECP256R1())
    pub = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    u = urlparse(endpoint)
    head = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    claims = b64u(json.dumps({"aud": f"{u.scheme}://{u.netloc}", "exp": int(time.time()) + 12 * 3600,
                              "sub": "https://sasha-apps.github.io/Arbeitstag/"}, separators=(",", ":")).encode())
    r, s = decode_dss_signature(key.sign(f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256())))
    jwt = f"{head}.{claims}.{b64u(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"
    return f"vapid t={jwt}, k={b64u(pub)}"


def send(sub, vapid_d, data, ttl=7200):
    body = encrypt(json.dumps(data, separators=(",", ":")).encode(), ub64(sub["keys"]["p256dh"]), ub64(sub["keys"]["auth"]))
    req = urllib.request.Request(sub["endpoint"], data=body, method="POST", headers={
        "Authorization": vapid_header(sub["endpoint"], vapid_d), "Content-Encoding": "aes128gcm",
        "Content-Type": "application/octet-stream", "TTL": str(ttl), "Urgency": "high"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0


# ---------- privates Daten-Repo ----------
def gh(path, method="GET", body=None):
    req = urllib.request.Request(API + path, method=method, data=json.dumps(body).encode() if body is not None else None, headers={
        "Authorization": "Bearer " + TOKEN, "Accept": "application/vnd.github+json", "User-Agent": UA,
        **({"Content-Type": "application/json"} if body is not None else {})})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, None


def get_file(name):
    st, j = gh(f"/repos/{REPO}/contents/{name}")
    if st == 404:
        return None, None
    if st != 200 or not j:
        raise RuntimeError(f"Lesen von {name}: HTTP {st}")
    return json.loads(base64.b64decode(j["content"]).decode()), j["sha"]


def put_file(name, obj, sha):
    body = {"message": "push", "content": base64.b64encode(json.dumps(obj, separators=(",", ":")).encode()).decode()}
    if sha:
        body["sha"] = sha
    return gh(f"/repos/{REPO}/contents/{name}", "PUT", body)[0]


# ---------- Verkehr (Autobahn GmbH des Bundes) ----------
NAMES = {"W": "Osnabrück", "E": "Bad Oeynhausen"}
# Orte entlang der A30 von West nach Ost (für die Fahrtrichtung aus „X -> Y“)
A30_ORDER = ["amsterdam", "hengelo", "oldenzaal", "bad bentheim", "gildehaus", "schüttorf", "salzbergen", "rheine", "hörstel",
             "ibbenbüren", "lotte", "osnabrück", "hasbergen", "hellern", "sutthausen", "nahne", "gesmold", "melle", "riemsloh",
             "bruchmühlen", "rödinghausen", "kirchlengern", "hiddenhausen", "bünde", "löhne", "bad oeynhausen", "hannover"]


def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def _pos(name):
    n = name.lower()
    for i, k in enumerate(A30_ORDER):
        if k in n:
            return i
    return None


def item_dirs(it):
    """Fahrtrichtung: W (Richtung Osnabrück) oder E (Richtung Bad Oeynhausen), unbekannt = beide."""
    sub = (it.get("subtitle") or "").strip()
    m = re.match(r"^(.+?)\s*(?:->|→)\s*(.+)$", sub)
    if m:
        a, b = _pos(m.group(1)), _pos(m.group(2))
        if a is not None and b is not None and a != b:
            return {"E"} if b > a else {"W"}
    text = " ".join([it.get("title") or "", sub] + list(it.get("description") or [])).lower()
    m = re.search(r"\b(?:a\d+\s+)?([a-zäöüß .-]{3,30}?)\s+richtung\s+([a-zäöüß .-]{3,30})", text)
    if m:
        a, b = _pos(m.group(1)), _pos(m.group(2))
        if a is not None and b is not None and a != b:
            return {"E"} if b > a else {"W"}
    return {"W", "E"}


def section(it):
    """„X - Y“ aus dem Titel „A30 | X - Y“."""
    m = re.match(r"^\s*A\d+\s*\|\s*(.+?)\s+-\s+(.+?)\s*$", it.get("title") or "")
    return (m.group(1).strip(), m.group(2).strip()) if m else None


def describe(it, kind):
    sec = section(it)
    if sec:
        where = f"zwischen {sec[0]} und {sec[1]}"
    else:
        t = re.sub(r"^\s*A\d+\s*[-|]\s*", "", it.get("title") or "")
        t = re.sub(r"\s*[-:]?\s*A[ikl]D\s*\d+\s*$", "", t, flags=re.I).replace(" - ", ": ", 1)
        where = (": " + (t[:90].rsplit(" ", 1)[0] + "…" if len(t) > 90 else t)) if t else ""
    if kind == "closure" or str(it.get("isBlocked")).lower() == "true":
        word = "Sperrung"
    elif kind == "warning":
        typ = (it.get("abnormalTrafficType") or "").upper()
        word = "Stau" if ("STATIONARY" in typ or "QUEUING" in typ or "SLOW" in typ or not typ) else "Verkehrsmeldung"
    else:
        word = "Baustelle"
    s = (word + (" " + where if where.startswith("zwischen") else where)).strip()
    try:
        d = int(float(it.get("delayTimeValue") or 0))
    except Exception:
        d = 0
    if d > 0:
        s += f" (+{d} Min.)"
    return s


def on_route(it, places):
    """Liegt die Meldung auf der eingetragenen Strecke? Bei „X - Y“ müssen beide Enden zur Strecke gehören."""
    match = lambda s: any(p.lower() in s.lower() for p in places)
    sec = section(it)
    if sec:
        return match(sec[0]) and match(sec[1])
    text = " ".join([it.get("title") or "", it.get("subtitle") or ""] + list(it.get("description") or []))
    return match(text)


def traffic(cfg, direction, now):
    road = re.sub(r"[^A-Za-z0-9]", "", (cfg or {}).get("road") or "A30").upper()
    places = [p.strip() for p in re.split(r"[,;]", (cfg or {}).get("places") or "") if len(p.strip()) >= 3]
    if not places:
        return None
    hits = {"warning": [], "closure": [], "roadworks": []}
    for kind in hits:
        data = fetch_json(f"{AUTOBAHN}/{road}/services/{kind}")
        for it in data.get(kind) or []:
            if it.get("future") is True or not on_route(it, places):
                continue
            st = it.get("startTimestamp")
            if st:
                try:
                    from datetime import datetime
                    if datetime.fromisoformat(st.replace("Z", "+00:00")).timestamp() > now + 3600:
                        continue
                except Exception:
                    pass
            if direction not in item_dirs(it):
                continue
            line = describe(it, kind)
            if line and line not in hits[kind]:
                hits[kind].append(line)
    key = lambda s: s.split(" ", 1)[-1]
    seen = {key(x) for x in hits["closure"] + hits["warning"]}
    parts = hits["closure"] + hits["warning"] + [x for x in hits["roadworks"] if key(x) not in seen][:2]
    head = f"{road} → {NAMES.get(direction, direction)}: "
    if not parts:
        return head + "frei, keine Meldungen."
    if not hits["warning"] and not hits["closure"]:
        parts.append("kein Stau")
    return head + "; ".join(parts)


# ---------- Wetter ----------
WMO = {0: "klar", 1: "überwiegend klar", 2: "teils bewölkt", 3: "bedeckt", 45: "Nebel", 48: "Nebel mit Reif",
       51: "leichter Niesel", 53: "Nieselregen", 55: "starker Niesel", 56: "gefrierender Niesel", 57: "gefrierender Niesel",
       61: "leichter Regen", 63: "Regen", 65: "starker Regen", 66: "gefrierender Regen", 67: "gefrierender Regen",
       71: "leichter Schnee", 73: "Schneefall", 75: "starker Schneefall", 77: "Schneegriesel", 80: "Schauer",
       81: "Regenschauer", 82: "heftige Schauer", 85: "Schneeschauer", 86: "Schneeschauer", 95: "Gewitter",
       96: "Gewitter mit Hagel", 99: "Gewitter mit Hagel"}
FREEZE = {56, 57, 66, 67, 71, 73, 75, 77, 85, 86}


def wx_point(cfg):
    line = (cfg.get("rt") or {}).get("line") or []
    return (line[0][0], line[0][1]) if line else HOME


def forecast(lat, lon):
    return fetch_json(f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
                      "&hourly=temperature_2m,precipitation_probability,precipitation,weather_code,wind_gusts_10m"
                      "&timezone=Europe%2FBerlin&forecast_days=2")


def hours(fc, day, hs):
    h, out = fc["hourly"], []
    for hh in hs:
        k = f"{day}T{hh:02d}:00"
        if k in h["time"]:
            i = h["time"].index(k)
            out.append({"t": h["temperature_2m"][i], "p": h["precipitation_probability"][i] or 0, "mm": h["precipitation"][i] or 0,
                        "c": h["weather_code"][i], "g": h["wind_gusts_10m"][i] or 0})
    return out


def risks(hs):
    """Was die Fahrt am frühen Morgen schwer macht (4 bis 8 Uhr)."""
    if not hs:
        return []
    r, low = [], min(x["t"] for x in hs)
    wet = any(x["mm"] > 0 or x["p"] >= 50 for x in hs)
    if any(x["c"] in FREEZE for x in hs) or (low <= 1 and wet):
        r.append(f"Glättegefahr ({round(low)} °C)")
    elif low <= 0:
        r.append(f"Frost ({round(low)} °C), Scheiben freikratzen")
    g = max(x["g"] for x in hs)
    if g >= 60:
        r.append(f"Sturmböen bis {round(g)} km/h")
    if any(x["c"] in (45, 48) for x in hs):
        r.append("Nebel")
    if any(x["c"] >= 95 for x in hs):
        r.append("Gewitter")
    return r


def dwd_alerts(lat, lon, start, end):
    """DWD-Warnungen (über Bright Sky), die das Zeitfenster berühren."""
    out = []
    for a in fetch_json(f"https://api.brightsky.dev/alerts?lat={lat}&lon={lon}").get("alerts") or []:
        try:
            on = datetime.fromisoformat(a["onset"]).timestamp()
            ex = datetime.fromisoformat(a["expires"]).timestamp() if a.get("expires") else end
        except Exception:
            continue
        if on < end and ex > start and (a.get("severity") or "") in ("moderate", "severe", "extreme"):
            t = a.get("event_de") or a.get("headline_de")
            if t and t not in out:
                out.append(t.capitalize() if t.isupper() else t)
    return out


def weather_line(cfg, now):
    lat, lon = wx_point(cfg)
    day = datetime.fromtimestamp(now, BERLIN).date().isoformat()
    fc = forecast(lat, lon)
    hs = hours(fc, day, [6, 7])
    if not hs:
        return None
    s = f"🌤 {round(hs[0]['t'])} °C, {WMO.get(hs[0]['c'], '')}"
    pm = hours(fc, day, [15])
    if pm:
        s += f" · 15 Uhr {round(pm[0]['t'])} °C" + (f", Regen {pm[0]['p']} %" if pm[0]["p"] >= 30 else "")
    r = risks(hours(fc, day, [5, 6, 7, 8]))
    return s + (" · ⚠ " + ", ".join(r) if r else "")


def evening_warning(cfg, now):
    """Zwischen 19 und 22 Uhr vor einem Arbeitstag: Warnung, wenn es morgen früh glatt, stürmisch oder neblig wird."""
    loc = datetime.fromtimestamp(now, BERLIN)
    tom = (loc + timedelta(days=1)).date()
    if not (WARN_HOURS[0] <= loc.hour < WARN_HOURS[1]) or tom.weekday() >= 5:
        return None
    lat, lon = wx_point(cfg)
    r = risks(hours(forecast(lat, lon), tom.isoformat(), [4, 5, 6, 7, 8]))
    t0 = datetime(tom.year, tom.month, tom.day, 4, tzinfo=BERLIN).timestamp()
    try:
        r += [a for a in dwd_alerts(lat, lon, t0, t0 + 5 * 3600) if a not in r]
    except Exception:
        pass
    if not r:
        return None
    return {"id": f"wx-{tom.isoformat()}", "k": "wx", "t": "Morgen früh: " + r[0].split(" (")[0],
            "wx": "⚠ " + "; ".join(r) + ". Lieber etwas früher losfahren."}


def main():
    if not TOKEN:
        print("DATA_TOKEN fehlt, nichts zu tun.")
        return
    now = time.time()
    try:
        cfg, _ = get_file("push.json")
    except Exception as e:
        print("push.json nicht lesbar:", str(e)[:80])
        return
    if not cfg or not cfg.get("on") or not cfg.get("sub") or not (cfg.get("vapid") or {}).get("d"):
        print("Benachrichtigungen nicht aktiv.")
        return
    sent, sent_sha = get_file("sent.json")
    sent = sent or {}
    ids = {k: v for k, v in (sent.get("ids") or {}).items() if now - v < KEEP_IDS}
    due = sorted([it for it in cfg.get("items") or [] if it.get("id") and it.get("at", 0) <= now
                  and now - it["at"] <= WINDOW and it["id"] not in ids], key=lambda x: x["at"])
    wx_on = (cfg.get("opt") or {}).get("wx") is not False
    if wx_on:
        try:
            w = evening_warning(cfg, now)
            if w and w["id"] not in ids:
                due.append(w)
        except Exception as e:
            print("Wetter nicht abrufbar:", type(e).__name__)
    print(f"fällig: {len(due)}")
    if not due:
        return
    tr_cache, tr_err = {}, ""
    am = (cfg.get("tr") or {}).get("am") or "W"
    ok = err = 0
    dead = False
    for it in due:
        data = {"id": it["id"], "k": it.get("k"), "b": it.get("b")}
        if it.get("k") == "wx":
            data = {"id": it["id"], "k": "wx", "t": it["t"], "wx": it["wx"]}
        if it.get("k") == "am" and wx_on:
            try:
                data["wx"] = weather_line(cfg, now)
            except Exception as e:
                tr_err = tr_err or type(e).__name__
        if it.get("k") in ("am", "pm"):
            d = am if it["k"] == "am" else ("E" if am == "W" else "W")
            if d not in tr_cache:
                try:
                    tr_cache[d] = traffic(cfg.get("tr"), d, now)
                except Exception as e:
                    tr_cache[d] = "Verkehrsmeldungen gerade nicht abrufbar."
                    tr_err = type(e).__name__
            if tr_cache[d]:
                data["tr"] = tr_cache[d]
        st = send(cfg["sub"], cfg["vapid"]["d"], data)
        if st in (200, 201, 202, 204):
            ok += 1
            ids[it["id"]] = int(now)
        elif st in (404, 410):
            dead = True
            break
        else:
            err = st or -1
    print(f"gesendet: {ok}, Fehler: {err or '-'}, abgemeldet: {dead}")
    out = {"ids": ids, "last": int(now) if ok else sent.get("last"), "dead": dead, "err": err or "", "trErr": tr_err, "run": int(now)}
    for _ in range(2):
        if put_file("sent.json", out, sent_sha) in (200, 201):
            break
        _, sent_sha = get_file("sent.json")


def probe():
    """Prüft das Format der öffentlichen Autobahn-Daten (keine persönlichen Angaben)."""
    for kind in ("warning", "closure", "roadworks"):
        data = fetch_json(f"{AUTOBAHN}/A30/services/{kind}").get(kind) or []
        print(f"{kind}: {len(data)} Einträge")
        for it in data[:4]:
            print("  Felder:", sorted(it.keys()))
            print("  Titel:", it.get("title"), "| Untertitel:", it.get("subtitle"), "| Richtung erkannt:", sorted(item_dirs(it)))
            print("  Text:", describe(it, kind))


if __name__ == "__main__":
    probe() if "--probe" in sys.argv else main()
