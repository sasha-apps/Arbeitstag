# ArbeitsTag

Ruhige To-do-App mit wiederkehrenden Aufgaben, Tagesring und Welt-Tab mit Nachrichten.

- App: https://sasha-apps.github.io/Arbeitstag/
- Daten liegen auf dem Gerät (Browser-Speicher). Sicherung über ⋯ → „Sicherung speichern“.
- Optionaler Abgleich zwischen Geräten: Daten werden auf dem Gerät mit AES-256-GCM verschlüsselt (Schlüssel per PBKDF2 aus einem Passwort, das das Gerät nie verlässt) und als `arbeitstag.sync` in einem separaten privaten Repo abgelegt.
- Benachrichtigungen: `.github/workflows/push.yml` liest alle 15 Minuten `push.json` aus dem privaten Repo (Secret `DATA_TOKEN`) und verschickt fällige Meldungen per Web Push. Die Texte sind auf dem Gerät verschlüsselt; angehängt wird nur die Verkehrslage (Autobahn GmbH des Bundes).
- QR-Code: qrcode-generator (MIT), QR-Scanner: jsQR (Apache-2.0), siehe LICENSE-Dateien.
- `news.json` wird stündlich von GitHub Actions aktualisiert (`.github/workflows/news.yml`). Gespeichert werden nur Schlagzeile, Anriss, Quelle, Zeit, Link und ggf. die Adresse des Vorschaubilds (das Bild selbst wird nicht kopiert).
