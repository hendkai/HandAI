# Skills-Hub — ein Ort, alle Tools

HandAI hat **einen** zentralen Skills-Ordner (den *Hub*). Du installierst Skills
aus dem Internet einmal dorthin, und jedes Agent-Tool wird auf genau diesen Ort
verlinkt — installiert = überall sichtbar.

## Der eine Ort
- Default: `$HANDAI_STATE/skills` → am Gerät `/data/handai/skills` (**persistent**,
  übersteht Rootfs-Updates). Überschreibbar via `skills.dir` in der Config oder
  `$HANDAI_SKILLS`.
- Beim Start jeder Agent-Sitzung exportiert das Cockpit `HANDAI_SKILLS=<hub>`, damit
  Tools/MCP den Ort auch per Env finden.

## Aus dem Internet installieren (Cockpit → *Skills* → *Install from internet*)
Unterstützte Quellen (per On-Screen-Keyboard eingegeben):
| Eingabe | wird zu |
|---|---|
| `owner/repo` | GitHub-Repo (git clone, flach) |
| `owner/repo@tag` | GitHub-Repo auf Tag/Branch/Commit |
| `https://…/x.git`, `git@host:x.git` | git clone |
| `https://…/x.tar.gz`, `.tgz`, `.tar` | Download + Entpacken |
| `https://…/x.zip` | Download + Entpacken |
| `~/pfad`, `/abs/pfad`, `./rel` | lokaler Ordner (kopieren) |

Ablauf: HandAI **löst die Quelle auf und zeigt sie an**, du bestätigst mit A
(*Yes, download*), dann wird in den Hub installiert und **automatisch zu allen Tools
gesynct**. Re-Install desselben Namens = Update.

## Wie die Tools den Hub nutzen (Adapter)
Jeder Provider deklariert in der Config, wo *er* Skills sucht:
```json
{ "id": "claude", "skills_dir": "~/.claude/skills" }
```
*Skills → Sync to tools* legt dann einen **Symlink** `~/.claude/skills → <hub>`. Etwas
Echtes, das dort schon liegt, wird nach `*.handai-bak` gesichert. So teilen sich alle
Tools denselben Bestand. Provider ohne `skills_dir` werden nicht verlinkt (z.B. reine
Remote-Agents, die ihre Skills auf dem Host haben).

Die vordefinierten Pfade (`~/.claude/skills`, `~/.codex/skills`,
`~/.config/opencode/skills`) sind sinnvolle Defaults — an die reale Skill-Konvention
des jeweiligen Tools anpassen (reine Config, kein Code).

## Sicherheit
- **Download braucht Bestätigung**: das Cockpit zeigt die aufgelöste Quelle, bevor
  irgendetwas geladen wird. Nur `http(s)`-URLs.
- **Kein Auto-Ausführen**: HandAI legt nur Dateien ab und führt keine Install-Skripte
  aus dem Skill aus. Was ein Skill tut, entscheidet der Agent, der ihn nutzt.
- **Path-Traversal-Schutz**: Archive werden mit Guard entpackt — ein `../`-Eintrag,
  der aus dem Hub ausbrechen will, wird abgelehnt (getestet).
- Skills kommen aus dem Internet: installiere nur, was du dir ansiehst. Der Hub liegt
  offen unter `$HANDAI_STATE/skills`, du kannst jederzeit reinschauen.

## Remote-Modi (*Skills → Sync to remote hosts*)
Für ssh-Agenten (hermes, codex-remote, claude/codex über devbox) wird der Hub auf den
Remote-Host **gespiegelt** und dort verlinkt:
1. HandAI ermittelt je Remote-Host, welche Provider dort laufen *und* ein `skills_dir`
   haben (`skills.remote_targets`).
2. **`sync_hub`** spiegelt den lokalen Hub nach `~/.local/state/handai/skills` auf dem
   Host — per **rsync** (`-az --delete`), mit **tar-über-ssh** als Fallback, wenn kein
   rsync da ist.
3. **`link_remote`** legt auf dem Host `skills_dir → gespiegelter Hub` als Symlink an
   (Vorhandenes wird nach `*.handai-bak` gesichert).

Im Menü wählst du einen Host oder *ALL hosts*; danach siehst du pro Host/Tool ✓/✗.
So sehen lokale *und* remote Tools denselben Skill-Bestand. Voraussetzung: ssh-Key-Login
zum Host (BatchMode, kein Passwortprompt).
