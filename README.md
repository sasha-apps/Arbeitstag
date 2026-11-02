# ArbeitsTag

Ruhige To-do-App mit wiederkehrenden Aufgaben, Tagesring, Morgenbriefing und Nachrichten.

- App: https://sasha-apps.github.io/Arbeitstag/
- Daten liegen auf dem Gerät (Browser-Speicher). Sicherung über ⋯ → „Sicherung speichern“.
- Optionaler Abgleich zwischen Geräten: Daten werden auf dem Gerät mit AES-256-GCM verschlüsselt (Schlüssel per PBKDF2 aus einem Passwort, das das Gerät nie verlässt) und als `arbeitstag.sync` in einem separaten privaten Repo abgelegt.
- Benachrichtigungen: `.github/workflows/push.yml` liest alle 15 Minuten `push.json` aus dem privaten Repo (Secret `DATA_TOKEN`) und verschickt fällige Meldungen per Web Push. Die Texte sind auf dem Gerät verschlüsselt; angehängt wird nur die Verkehrslage (Autobahn GmbH des Bundes).
- QR-Code: qrcode-generator (MIT), QR-Scanner: jsQR (Apache-2.0), siehe LICENSE-Dateien.
- `news.json` wird stündlich von GitHub Actions aktualisiert (`.github/workflows/news.yml`). Gespeichert werden nur Schlagzeile, Anriss, Quelle, Zeit, Link und ggf. die Adresse des Vorschaubilds (das Bild selbst wird nicht kopiert).
- Morgenbriefing auf „Heute“: Wetter von [Open-Meteo](https://open-meteo.com) und DWD-Warnungen über [Bright Sky](https://brightsky.dev), Verkehr auf der A30 von der Autobahn GmbH des Bundes, Müllabfuhr, Spritpreise und Berichtsheft. Alles wird direkt vom Gerät abgerufen.
- `muell.json` holt `.github/workflows/muell.yml` jeden Montag aus dem Abfuhrkalender der [Gemeinde Rödinghausen](https://www.roedinghausen.de/Service/Abfall/), für alle Straßen. Die eigene Straße wird nur auf dem Gerät gewählt. Am Vorabend steht die Tonne als Aufgabe mit 🔔 in der Liste.
- Spritpreise von [Tankerkönig](https://creativecommons.tankerkoenig.de) (MTS-K, CC BY 4.0). Der eigene API-Schlüssel wird im Menü eingetragen und bleibt auf dem Gerät.
- Wetter in der Benachrichtigung um 5:30 und eine Warnung am Vorabend eines Arbeitstags (19 bis 22 Uhr) bei Glätte, Sturm, Nebel oder DWD-Warnung kommen aus `send_push.py`.
- Gym: Trainingsplan nach der Regel 2-5-15 (Torso/Limbs, als Text änderbar), jeden Satz mit kg und Wiederholungen abhaken, Vorschläge vom letzten Mal, Pausentimer, Verlauf. Liegt verschlüsselt als `gym.sync` im privaten Repo (gleicher Schlüssel wie der Abgleich).
- Berichtsheft: Einträge je Tag liegen verschlüsselt als `berichtsheft.sync` im privaten Repo (gleicher Schlüssel wie der Abgleich). `tools/berichtsheft_import.py` holt sie auf dem Laptop in die Tagesnotizen im Vault (braucht `gh` und `pip install cryptography`).
