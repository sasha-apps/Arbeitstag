#!/usr/bin/env python3
"""Holt die Berichtsheft-Einträge aus ArbeitsTag in die Tagesnotizen im Obsidian-Vault.

Die App legt die Einträge verschlüsselt als berichtsheft.sync im privaten Daten-Repo ab.
Dieses Skript lädt die Datei mit `gh` (muss angemeldet sein), fragt nach dem Abgleich-Passwort
und schreibt jeden Tag in <Vault>/<Ordner>/JJJJ-MM-TT.md unter „## Gemacht“. Der Abschnitt
aus der App steht zwischen %% ArbeitsTag %%-Markierungen und wird bei jedem Lauf ersetzt;
alles andere in der Notiz bleibt, wie es ist.

    py -3 tools/berichtsheft_import.py              # letzte 14 Tage
    py -3 tools/berichtsheft_import.py --tage 60
    py -3 tools/berichtsheft_import.py --merken     # Schlüssel auf diesem Laptop merken

Braucht: pip install cryptography
"""
import argparse, base64, getpass, gzip, json, os, re, shutil, subprocess, sys
from datetime import date, timedelta
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

REPO = "sasha-apps/arbeitstag-daten"
FILE = "berichtsheft.sync"
VAULT = Path(__file__).resolve().parents[3] / "Vault"          # John/Code/Arbeitstag/tools → John/Vault
FOLDER = "10 Arbeit/Tagesnotizen"
TEMPLATE = "Vorlagen/Tagesnotiz.md"
KEYFILE = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ArbeitsTag" / "schluessel.txt"
START, END = "%% ArbeitsTag %%", "%% /ArbeitsTag %%"


def gh_exe():
    exe = shutil.which("gh") or r"C:\Program Files\GitHub CLI\gh.exe"
    if not Path(exe).exists() and not shutil.which("gh"):
        sys.exit("GitHub CLI (gh) nicht gefunden.")
    return exe


def load_envelope():
    r = subprocess.run([gh_exe(), "api", f"repos/{REPO}/contents/{FILE}", "--jq", ".content"],
                       capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        if "404" in r.stderr:
            sys.exit("Noch keine Berichtsheft-Einträge im Repo. Erst in der App etwas eintragen (mit Abgleich).")
        sys.exit("Abruf über gh fehlgeschlagen: " + r.stderr.strip()[:200])
    return json.loads(base64.b64decode(r.stdout.strip()))


def derive(password, env):
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=base64.b64decode(env["salt"]), iterations=int(env.get("it", 210000)))
    return kdf.derive(password.encode("utf-8"))


def open_envelope(env, key):
    data = AESGCM(key).decrypt(base64.b64decode(env["iv"]), base64.b64decode(env["ct"]), None)
    if env.get("z"):
        data = gzip.decompress(data)
    return json.loads(data.decode("utf-8"))


def get_key(env, remember):
    if KEYFILE.exists():
        try:
            key = base64.b64decode(KEYFILE.read_text().strip())
            return key, open_envelope(env, key)
        except Exception:
            print("Gemerkter Schlüssel passt nicht mehr, bitte Passwort eingeben.")
    for _ in range(3):
        key = derive(getpass.getpass("Abgleich-Passwort aus ArbeitsTag: "), env)
        try:
            data = open_envelope(env, key)
        except Exception:
            print("Passwort passt nicht.")
            continue
        if remember:
            KEYFILE.parent.mkdir(parents=True, exist_ok=True)
            KEYFILE.write_text(base64.b64encode(key).decode())
            print(f"Schlüssel gemerkt in {KEYFILE}")
        return key, data
    sys.exit("Abgebrochen.")


def block(text):
    lines = [re.sub(r"^\s*[-*•]\s*", "", l).strip() for l in text.splitlines()]
    return "\n".join([START] + [f"- {l}" for l in lines if l] + [END])


def write_day(day, text):
    path = VAULT / FOLDER / f"{day}.md"
    new = block(text)
    if path.exists():
        doc = path.read_text(encoding="utf-8")
    else:
        tpl = VAULT / TEMPLATE
        doc = tpl.read_text(encoding="utf-8") if tpl.exists() else "# {{date}}\n\n## Gemacht\n- \n"
        doc = doc.replace("{{date}}", day)
    if START in doc and END in doc:
        out = re.sub(re.escape(START) + r".*?" + re.escape(END), lambda m: new, doc, flags=re.S)
    elif "## Gemacht" in doc:
        # leere Aufzählung der Vorlage („- “) ersetzen, sonst direkt unter die Überschrift
        out = re.sub(r"(## Gemacht[^\n]*\n)(?:-[ \t]*\n)?", lambda m: m.group(1) + new + "\n", doc, count=1)
    else:
        out = doc.rstrip() + "\n\n## Gemacht\n" + new + "\n"
    if out == doc:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(out, encoding="utf-8", newline="\n")
    return True


def main():
    ap = argparse.ArgumentParser(description="Berichtsheft-Einträge aus ArbeitsTag in die Tagesnotizen holen")
    ap.add_argument("--tage", type=int, default=14, help="so viele Tage zurück (Standard 14)")
    ap.add_argument("--merken", action="store_true", help="Schlüssel auf diesem Laptop speichern")
    a = ap.parse_args()
    env = load_envelope()
    _, data = get_key(env, a.merken)
    since = (date.today() - timedelta(days=a.tage)).isoformat()
    n = 0
    for day, e in sorted((data.get("e") or {}).items()):
        if day < since or not (e.get("t") or "").strip():
            continue
        if write_day(day, e["t"]):
            n += 1
            print(f"  {day} geschrieben")
    print(f"Fertig: {n} Tagesnotizen aktualisiert in {VAULT / FOLDER}")


if __name__ == "__main__":
    main()
