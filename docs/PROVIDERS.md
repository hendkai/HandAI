# Provider hinzufügen & Auth

## Einen neuen Provider hinzufügen (kein Code)
Eintrag in `~/.config/handai/handai.json` unter `providers`:

```json
{
  "id": "meinagent",
  "label": "Mein Agent",
  "command": ["meinagent", "run"],
  "auth": "token-env",
  "token_env": "MEINAGENT_KEY",
  "allowed_modes": ["devbox", "cloud"]
}
```

Felder:
- `command` — argv zum Starten (bereits gesplittet, kein Shell-String).
- `auth` — bevorzugte Methode: `oauth-device` | `token-env` | `none`.
- `auth_methods` — optional mehrere angebotene Methoden, zum Beispiel
  `["oauth-device", "token-env"]`. Im Login-Menü erscheinen dann OAuth **und**
  API-Key nebeneinander. Alte Configs mit nur `auth` bleiben kompatibel.
- `token_env` — nur bei `token-env`: Env-Var, aus der die CLI ihr Credential liest.
- `login_command` — nur bei `oauth-device`: argv des interaktiven Login-Flows.
- `allowed_modes` — leer = alle Modi erlaubt; sonst Whitelist von Modus-IDs.

## Auth-Fluss im Cockpit
- **oauth-device**: *Providers/Login* → Provider wählen → HandAI startet
  `login_command` interaktiv. Der Provider zeigt Device-Code + URL, du öffnest sie am
  Handy. Kein Token wird in HandAI gespeichert.
- **token-env**: *Providers/Login* → Provider wählen → On-Screen-Keyboard → Token wird
  im Secret-Store (`$HANDAI_STATE/secrets.json`, 0600) abgelegt.

OAuth kann wahlweise auf dem Handheld oder direkt auf einem erlaubten SSH-Host
gestartet werden. Das ist wichtig, weil die CLI ihre erneuerbaren OAuth-Daten auf
dem Rechner speichert, auf dem später auch der Agent läuft. Auf dem Handheld zeigt
`HOME` nach `/data/handai/home`, sodass CLI-Logins Image-Updates überleben.

Das Login-Menü trennt **Local Providers** und **Remote Providers**. Im Remote-Bereich
wird zuerst Devbox oder Cloud gewählt; anschließend erscheinen ausschließlich die
für dieses Ziel erlaubten Provider und die Login-Aktion wird direkt dort ausgeführt.

Die ausgelieferte Beispielkonfiguration bietet beide Wege für alle sechs Einträge:

- Claude Code: Anthropic-OAuth oder `ANTHROPIC_API_KEY`.
- Codex: ChatGPT/Device-OAuth oder `OPENAI_API_KEY`.
- Hermes: Nous-Portal-OAuth oder `OPENROUTER_API_KEY`.
- OpenCode: interaktiver Provider-Login oder `OPENCODE_API_KEY`.
- OpenClaw: interaktive Modell-Anmeldung oder `OPENAI_API_KEY`.

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

OAuth bleibt der normale Loginweg. Manuell hinterlegte Zugriffstokens befinden sich
im GUI ausschließlich unter **Advanced**. Der Secret-Store kann in den Settings mit
einem Boot-PIN verschlüsselt werden.

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
| id            | auth         | Modi           | Anmerkung                                  |
|---------------|--------------|----------------|--------------------------------------------|
| claude        | OAuth + Key  | local, devbox, cloud | `claude login` / `ANTHROPIC_API_KEY`  |
| codex         | OAuth + Key  | local, devbox  | Device-Auth / `OPENAI_API_KEY`             |
| codex-remote  | OAuth + Key  | devbox, cloud  | Device-Auth / `OPENAI_API_KEY`             |
| hermes        | OAuth + Key  | local, devbox, cloud | Nous Portal / `OPENROUTER_API_KEY`    |
| opencode      | OAuth + Key  | local, devbox  | Provider-Login / `OPENCODE_API_KEY`        |
| openclaw      | OAuth + Key  | local, devbox, cloud | Modell-Login / `OPENAI_API_KEY`       |

Die exakten `command`/`login_command`/`token_env` je Tool ggf. an die reale CLI
anpassen — die genannten sind sinnvolle Defaults, keine garantierten Flags.
