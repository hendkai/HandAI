# Provider hinzufügen & Auth

## Einen neuen Provider hinzufügen (kein Code)
Eintrag in `~/.config/handai/handai.json` unter `providers`:

```json
{
  "id": "meinagent",
  "label": "Mein Agent",
  "command": ["meinagent", "run"],
  "auth": "oauth-device",
  "oauth_profiles": [
    {
      "label": "MEIN ABO",
      "command": ["meinagent", "login", "--device-code"]
    }
  ],
  "allowed_modes": ["devbox", "cloud"]
}
```

Felder:
- `command` — argv zum Starten (bereits gesplittet, kein Shell-String).
- `auth` — bevorzugte Methode: `oauth-device` | `token-env` | `none`.
- `auth_methods` — optionale Liste unterstützter Methoden. Die ausgelieferte
  Konfiguration verwendet nur `["oauth-device"]`.
- `oauth_profiles` — ein oder mehrere konkrete Account-/Abo-Logins mit Label und
  argv. Bei mehreren Profilen zeigt das Pixel-GUI zuerst eine Auswahl.
- `login_command` — rückwärtskompatibler einzelner OAuth-Befehl; neue Einträge
  sollten `oauth_profiles` verwenden.
- `token_env` — nur für eigene `token-env`-Provider; nicht Teil der Standard-GUI.
- `allowed_modes` — leer = alle Modi erlaubt; sonst Whitelist von Modus-IDs.

## Auth-Fluss im Cockpit
- **oauth-device**: *Providers/Login* → Provider wählen → HandAI startet
  die ausgewählte Provider-CLI unsichtbar im Hintergrund. URL, QR-Code,
  Device-Code und Live-Status erscheinen im 640×480-Pixel-GUI. Wenn ein Browser
  einen Rückgabecode anzeigt, sendet **A → Paste Login Code** ihn über die
  Bildschirmtastatur an die wartende CLI. Kein OAuth-Token wird in HandAI
  gespeichert oder angezeigt.
- **token-env**: bleibt als Konfigurationsmöglichkeit für eigene Integrationen
  erhalten, wird von den mitgelieferten AI-Providern aber nicht angeboten.

OAuth kann wahlweise auf dem Handheld oder direkt auf einem erlaubten SSH-Host
gestartet werden. Das ist wichtig, weil die CLI ihre erneuerbaren OAuth-Daten auf
dem Rechner speichert, auf dem später auch der Agent läuft. Auf dem Handheld zeigt
`HOME` nach `/data/handai/home`, sodass CLI-Logins Image-Updates überleben.

Das Login-Menü trennt **Local Providers** und **Remote Providers**. Im Remote-Bereich
wird zuerst Devbox oder Cloud gewählt; anschließend erscheinen ausschließlich die
für dieses Ziel erlaubten Provider und die Login-Aktion wird direkt dort ausgeführt.

Die ausgelieferte Beispielkonfiguration bietet ausschließlich echte
Account-/Subscription-Flows:

- Claude Code: Claude-Subscription über `claude auth login`.
- Codex: ChatGPT/Codex-Subscription über `codex login --device-auth`.
- Hermes: Nous Portal, OpenAI Codex oder xAI über `hermes auth add … --type oauth`.
- OpenCode: ChatGPT Pro/Plus, SuperGrok oder GitHub Copilot; alle Auswahlen
  werden vorab als headless/device-tauglicher CLI-Aufruf festgelegt.
- OpenClaw: ChatGPT/Codex über den offiziellen `--device-code`-Flow.

## Verwaltete Remoteziele

Unter **Settings → Remote Devices** können ohne JSON-Änderung drei Zieltypen angelegt werden:

- **SSH Device**: HandAI erzeugt bei Bedarf einen Ed25519-Key, startet `ssh-copy-id`
  interaktiv und prüft anschließend Key-Login plus `tmux`.
- **OpenClaw Gateway**: direkter TUI-Zugriff über `OPENCLAW_GATEWAY_URL` und
  `OPENCLAW_GATEWAY_TOKEN`. Öffentlich ist nur `wss://` zulässig; privates LAN,
  mDNS und Tailnet dürfen `ws://` verwenden.
- **Hermes Server**: direkter Terminal-Client für die offizielle Sessions API eines
  `hermes serve`-Backends. Öffentlich ist `https://` Pflicht; `http://` bleibt auf
  private LAN-/Tailscale-Adressen beschränkt.

OAuth ist der normale Loginweg. Gateway- und Remote-Service-Tokens sind davon
getrennte Zugangsdaten und bleiben im PIN-fähigen Secret-Store.

## Remote-Token-Bereitstellung (wichtig)
Bei `token-env` **+ ssh-Modus** darf das Token **nicht** inline im ssh-Kommando stehen
(es wäre in der Prozessliste des Remote-Hosts sichtbar). Empfohlene Wege:

1. **Token liegt schon auf dem Remote-Host** (bevorzugt): Der Agent liest es dort aus
   seiner eigenen Config/Env. HandAI muss dann gar kein Token halten → Provider auf
   `auth: "none"` setzen und das Handheld ist nur Fernbedienung.
2. **Einmalige Provisionierung**: Token per `ssh host 'umask 077; cat > ~/.config/…'`
   einmal hinterlegen (geplantes Cockpit-Feature „push token to host"), danach liest
   die Remote-CLI es lokal.
3. **SendEnv/SetEnv** nur, wenn der Remote-`sshd` `AcceptEnv` für die Var erlaubt —
   sonst wird die Var stillschweigend verworfen.

> Lokaler Modus ist unkritisch: der Router setzt die Env-Var im Kindprozess auf dem
> Gerät selbst (`cockpit._env_prefix`), sie taucht nur lokal auf.

## Die aktuell vordefinierten Provider (`config/handai.example.json`)
| id            | auth       | Modi           | Native GUI-Profile |
|---------------|------------|----------------|--------------------|
| claude        | OAuth only | local, devbox, cloud | Claude Subscription |
| codex         | OAuth only | local, devbox  | ChatGPT/Codex Device Code |
| codex-remote  | OAuth only | devbox, cloud  | ChatGPT/Codex Device Code |
| hermes        | OAuth only | local, devbox, cloud | Nous, OpenAI Codex, xAI |
| opencode      | OAuth only | local, devbox  | ChatGPT, SuperGrok, GitHub Copilot |
| openclaw      | OAuth only | local, devbox, cloud | OpenAI ChatGPT/Codex Device Code |

Die Profile wurden gegen die im Juli 2026 installierten CLI-Hilfen und die
jeweilige Primärdokumentation geprüft. Sie sind datengetrieben, damit spätere
CLI-Änderungen ohne Umbau des GUI-Runners angepasst werden können.
