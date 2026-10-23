#!/usr/bin/env python3
"""Verschickt geplante ArbeitsTag-Benachrichtigungen per Web Push.

Liest push.json aus dem privaten Daten-Repo (Token in DATA_TOKEN). Die Texte der
Erinnerungen sind auf dem Gerät mit dem Passwort-Schlüssel verschlüsselt; dieses
Skript sieht nur Zeitpunkte und den verschlossenen Inhalt. Für die Morgen- und
Feierabend-Meldung hängt es Verkehrsmeldungen der Autobahn GmbH an.
Gibt bewusst nichts Persönliches im Log aus (das Repo ist öffentlich).
"""
import base64, hashlib, hmac, json, os, re, sys, time, urllib.error, urllib.request
from urllib.parse import urlparse

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
DIRS = {"W": r"amsterdam|osnabr[üu]ck|rheine|bad bentheim|hengelo|niederlande|lotte|ibbenb[üu]ren|salzbergen",
        "E": r"hannover|bad oeynhausen|l[öo]hne|b[üu]nde|kirchlengern|herford|minden"}
NAMES = {"W": "Osnabrück", "E": "Bad Oeynhausen"}


def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def item_dirs(text):
    t = text.lower()
    if re.search(r"beide[nr]? (fahrt)?richtung", t):
        return {"W", "E"}
    found = set()
    for m in re.finditer(r"(?:richtung|->|→|>)\s*([a-zäöüß .-]{3,40})", t):
        for k, rx in DIRS.items():
            if re.match(r"\s*(?:" + rx + ")", m.group(1)):
                found.add(k)
    return found or {"W", "E"}


def between(it):
    for line in it.get("description") or []:
        m = re.search(r"zwischen\s+(.+?)\s+und\s+(.+?)(?:[,.;]|$)", line, re.I)
        if m:
            return f"zwischen {m.group(1).strip()} und {m.group(2).strip()}"
    t = it.get("title") or ""
    if "|" in t:
        t = t.split("|", 1)[1]
    t = re.sub(r"\bAS\s+", "", t).strip()
    t = re.sub(r"\s*\(\d+\)", "", t)
    return ("bei " + t) if t else ""


def describe(it, kind):
    where = between(it)
    if kind == "closure" or str(it.get("isBlocked")).lower() == "true":
        word = "Sperrung"
    elif kind == "warning":
        typ = (it.get("abnormalTrafficType") or "").upper()
        word = "Stau" if ("STATIONARY" in typ or "QUEUING" in typ or "SLOW" in typ or not typ) else "Verkehrsmeldung"
    else:
        word = "Baustelle"
    s = f"{word} {where}".strip()
    try:
        d = int(float(it.get("delayTimeValue") or 0))
    except Exception:
        d = 0
    if d > 0:
        s += f" (+{d} Min.)"
    return s


def traffic(cfg, direction, now):
    road = re.sub(r"[^A-Za-z0-9]", "", (cfg or {}).get("road") or "A30").upper()
    places = [p.strip() for p in re.split(r"[,;]", (cfg or {}).get("places") or "") if len(p.strip()) >= 3]
    if not places:
        return None
    rx = re.compile("|".join(re.escape(p) for p in places), re.I)
    hits = {"warning": [], "closure": [], "roadworks": []}
    for kind in hits:
        data = fetch_json(f"{AUTOBAHN}/{road}/services/{kind}")
        for it in data.get(kind) or []:
            text = " ".join([it.get("title") or "", it.get("subtitle") or ""] + list(it.get("description") or []))
            if not rx.search(text):
                continue
            if it.get("future") is True:
                continue
            st = it.get("startTimestamp")
            if st:
                try:
                    from datetime import datetime
                    if datetime.fromisoformat(st.replace("Z", "+00:00")).timestamp() > now + 3600:
                        continue
                except Exception:
                    pass
            if direction not in item_dirs(text):
                continue
            line = describe(it, kind)
            if line and line not in hits[kind]:
                hits[kind].append(line)
    parts = hits["closure"] + hits["warning"] + hits["roadworks"][:2]
    head = f"{road} → {NAMES.get(direction, direction)}: "
    if not parts:
        return head + "frei, keine Meldungen."
    if not hits["warning"] and not hits["closure"]:
        parts.append("kein Stau")
    return head + "; ".join(parts)


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
    print(f"fällig: {len(due)}")
    if not due:
        return
    tr_cache, tr_err = {}, ""
    am = (cfg.get("tr") or {}).get("am") or "W"
    ok = err = 0
    dead = False
    for it in due:
        data = {"id": it["id"], "k": it.get("k"), "b": it.get("b")}
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


if __name__ == "__main__":
    main()
