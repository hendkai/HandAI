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
- `auth` — `oauth-device` | `token-env` | `none`.
- `token_env` — nur bei `token-env`: Env-Var, aus der die CLI ihr Credential liest.
- `login_command` — nur bei `oauth-device`: argv des interaktiven Login-Flows.
- `allowed_modes` — leer = alle Modi erlaubt; sonst Whitelist von Modus-IDs.

## Auth-Fluss im Cockpit
- **oauth-device**: *Providers/Login* → Provider wählen → HandAI startet
  `login_command` interaktiv. Der Provider zeigt Device-Code + URL, du öffnest sie am
  Handy. Kein Token wird in HandAI gespeichert.
- **token-env**: *Providers/Login* → Provider wählen → On-Screen-Keyboard → Token wird
  im Secret-Store (`$HANDAI_STATE/secrets.json`, 0600) abgelegt.

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
| claude        | oauth-device | local, devbox  | Claude Code CLI, `claude login`            |
| codex         | oauth-device | local, devbox  | Codex CLI, `codex login`                   |
| codex-remote  | token-env    | devbox, cloud  | `codex --cloud`, `OPENAI_API_KEY`          |
| hermes        | token-env    | devbox, cloud  | `hermes agent`, `HERMES_API_KEY` (remote)  |
| opencode      | token-env    | local, devbox  | `OPENCODE_API_KEY`                         |

Die exakten `command`/`login_command`/`token_env` je Tool ggf. an die reale CLI
anpassen — die genannten sind sinnvolle Defaults, keine garantierten Flags.
