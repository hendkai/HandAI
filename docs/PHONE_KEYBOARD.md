# Tailscale und Handy-Tastatur

## Tailscale anmelden

Im Pixel-Cockpit: **Network → Tailscale → Login with phone**. HandAI startet
`tailscale login --timeout=10s`, extrahiert ausschließlich eine URL unter
`https://login.tailscale.com/` und zeigt sie als QR-Code. Nach dem Scan wird die
Anmeldung im Browser des Handys abgeschlossen. Der Node-Schlüssel liegt dauerhaft
unter `/data/tailscale`, nicht in der HandAI-Konfiguration.

Der Tastatur-QR enthält nur ein einmal verwendbares Pairing-Geheimnis. Beim ersten
Aufruf tauscht der Handheld es gegen ein `HttpOnly`-/`SameSite=Strict`-Cookie aus und
entfernt das Geheimnis per Redirect aus der URL. Texteingaben benötigen zusätzlich
einen CSRF-Wert; Cache-, Referrer-, MIME- und Content-Security-Header sind gesetzt.
Die Kopplung endet standardmäßig nach 15 Minuten.

Das Geräteimage startet `tailscaled` nach dem Mount von `/data`. Status und Logout
sind im selben Menü verfügbar. Der Full- und der Remote-Build enthalten Tailscale
sowie das Kommandozeilenwerkzeug `qrencode`.

## Handy als Tastatur koppeln

1. Eine HandAI/tmux-Session starten oder geöffnet lassen.
2. Im Hauptmenü **Phone keyboard** wählen.
3. Die Ziel-Session wählen und den QR-Code mit dem Handy scannen.
4. Im Browser Text eingeben und **Send to Gameboy** drücken.
5. Mit **B** am Handheld wird die Freigabe sofort beendet.

HandAI bevorzugt eine aktive Tailscale-IPv4-Adresse; andernfalls verwendet es die
lokale WLAN-Adresse. Der Webdienst läuft höchstens 15 Minuten und besitzt pro Start
ein zufälliges 192-Bit-Token. Anfragen ohne dieses Token werden abgewiesen, Eingaben
sind auf 4096 Zeichen begrenzt. Text wird literal über `tmux load-buffer` bzw.
`send-keys -l` übertragen und nicht als lokale Shell-Zeile ausgewertet.

Bei Nutzung über die lokale WLAN-Adresse ist HTTP selbst nicht verschlüsselt. Diese
Variante sollte nur in einem vertrauenswürdigen Netz verwendet werden. Über die
Tailscale-Adresse läuft der Netzwerkpfad durch das verschlüsselte Tailnet; dies ist
die empfohlene Variante. Der Pairing-Link ist ein temporäres Geheimnis und sollte
nicht geteilt werden.
