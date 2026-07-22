"""Skills hub - one shared place for skills, installed from the internet, used
by every agent tool.

Design: a single canonical directory (the *hub*, default $HANDAI_STATE/skills,
persistent on /data on the device). Skills are installed into it from git repos,
tarballs, zips, GitHub shorthands, or local paths. Each agent tool is then
*linked* to the hub (symlink of the tool's own skills dir -> hub), so a skill
installed once is visible everywhere - that's the whole point.

Security notes:
- Installing pulls files from the internet. The cockpit shows the resolved
  source and asks for confirmation before fetching. HandAI never runs install
  scripts from a skill; it only places files. Agents decide what to use.
- Archives are extracted with a path-traversal guard (see safe_extract_*), so a
  malicious "../" entry cannot escape the hub.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.request import urlopen


# --- hub location -----------------------------------------------------------
def hub_dir(config_skills_dir: str | None = None) -> Path:
    raw = (
        os.environ.get("HANDAI_SKILLS")
        or config_skills_dir
        or os.path.join(
            os.environ.get("HANDAI_STATE") or os.path.expanduser("~/.local/state/handai"),
            "skills",
        )
    )
    p = Path(os.path.expandvars(os.path.expanduser(raw)))
    p.mkdir(parents=True, exist_ok=True)
    return p


def slugify(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9._-]+", "-", name).strip("-.").lower()
    return s or "skill"


# --- source parsing ---------------------------------------------------------
@dataclass(frozen=True)
class Source:
    kind: str          # git | tar | zip | local
    location: str      # url or path (normalised)
    name: str          # suggested install slug
    ref: str | None = None  # git branch/tag/commit


def parse_source(spec: str) -> Source:
    """Turn a user string into a typed Source. Supports:
      - GitHub shorthand:   owner/repo            owner/repo@v1.2
      - git urls:           https://.../x.git  git@host:x.git  git+https://...
      - archives:           https://.../x.tar.gz  .../x.tgz  .../x.zip
      - local paths:        /abs/dir  ./rel  ~/dir  (existing)
    """
    spec = spec.strip()

    # local path (existing) wins if it resolves
    lp = Path(os.path.expanduser(spec))
    if spec and (spec.startswith((".", "/", "~")) or lp.exists()):
        return Source("local", str(lp), slugify(lp.name))

    ref = None
    core = spec
    if spec.startswith("git+"):
        core = spec[4:]

    # GitHub shorthand owner/repo[@ref]
    m = re.fullmatch(r"([\w.-]+)/([\w.-]+)(?:@([\w./-]+))?", core)
    if m and not core.endswith((".tar.gz", ".tgz", ".tar", ".zip")):
        owner, repo, ref = m.group(1), m.group(2), m.group(3)
        repo = repo[:-4] if repo.endswith(".git") else repo
        return Source("git", f"https://github.com/{owner}/{repo}.git", slugify(repo), ref)

    low = core.lower()
    if low.endswith((".tar.gz", ".tgz", ".tar")):
        name = re.sub(r"\.(tar\.gz|tgz|tar)$", "", core.rsplit("/", 1)[-1], flags=re.I)
        return Source("tar", core, slugify(name))
    if low.endswith(".zip"):
        name = core.rsplit("/", 1)[-1][:-4]
        return Source("zip", core, slugify(name))
    if core.endswith(".git") or core.startswith("git@"):
        name = core.rsplit("/", 1)[-1]
        name = name[:-4] if name.endswith(".git") else name
        return Source("git", core, slugify(name))

    raise ValueError(f"unrecognised skill source: {spec!r}")


# --- safe archive extraction (path-traversal guarded) -----------------------
def _within(base: Path, candidate: Path) -> bool:
    base = base.resolve()
    try:
        candidate.resolve().relative_to(base)
        return True
    except ValueError:
        return False


def safe_extract_tar(tf: tarfile.TarFile, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for m in tf.getmembers():
        target = dest / m.name
        if not _within(dest, target):
            raise ValueError(f"unsafe path in archive: {m.name!r}")
        if m.islnk() or m.issym():
            link_dest = (dest / m.name).parent / m.linkname
            if not _within(dest, link_dest):
                raise ValueError(f"unsafe link in archive: {m.name!r} -> {m.linkname!r}")
    tf.extractall(dest)


def safe_extract_zip(zf: zipfile.ZipFile, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for name in zf.namelist():
        if not _within(dest, dest / name):
            raise ValueError(f"unsafe path in archive: {name!r}")
    zf.extractall(dest)


# --- installed-skill model + listing ----------------------------------------
@dataclass(frozen=True)
class Skill:
    name: str
    path: Path
    description: str = ""


def _read_manifest(path: Path) -> str:
    """Best-effort description from SKILL.md frontmatter or skill.json."""
    md = path / "SKILL.md"
    if md.exists():
        txt = md.read_text("utf-8", errors="replace")
        m = re.search(r"^description:\s*(.+)$", txt, flags=re.M)
        if m:
            return m.group(1).strip()
    js = path / "skill.json"
    if js.exists():
        import json
        try:
            return str(json.loads(js.read_text("utf-8")).get("description", "")).strip()
        except (ValueError, OSError):
            pass
    return ""


def list_installed(hub: Path) -> list[Skill]:
    out: list[Skill] = []
    for child in sorted(hub.iterdir() if hub.exists() else []):
        if child.is_dir() and not child.name.startswith("."):
            out.append(Skill(child.name, child, _read_manifest(child)))
    return out


def remove(hub: Path, name: str) -> bool:
    target = hub / name
    if target.is_dir() and _within(hub, target):
        shutil.rmtree(target)
        return True
    return False


# --- install ----------------------------------------------------------------
def install(hub: Path, spec: str, timeout: float = 60.0) -> Skill:
    """Fetch a skill into the hub. Returns the installed Skill.

    Raises ValueError/OSError on failure. Overwrites an existing skill of the
    same name (re-install = update).
    """
    src = parse_source(spec)
    dest = hub / src.name
    tmp = hub / (f".tmp-{src.name}")
    if tmp.exists():
        shutil.rmtree(tmp)

    if src.kind == "local":
        shutil.copytree(src.location, tmp)
    elif src.kind == "git":
        args = ["git", "clone", "--depth", "1"]
        if src.ref:
            args += ["--branch", src.ref]
        args += [src.location, str(tmp)]
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            raise ValueError(f"git clone failed: {r.stderr.strip()}")
        shutil.rmtree(tmp / ".git", ignore_errors=True)
    elif src.kind in ("tar", "zip"):
        data = _download(src.location, timeout)
        tmp.mkdir(parents=True)
        import io
        if src.kind == "tar":
            with tarfile.open(fileobj=io.BytesIO(data)) as tf:
                safe_extract_tar(tf, tmp)
        else:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                safe_extract_zip(zf, tmp)
        tmp = _flatten_single_dir(tmp)
    else:  # pragma: no cover - parse_source guards this
        raise ValueError(f"unsupported source kind: {src.kind}")

    if dest.exists():
        shutil.rmtree(dest)
    tmp.replace(dest)
    return Skill(src.name, dest, _read_manifest(dest))


def _download(url: str, timeout: float) -> bytes:
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError(f"refusing non-http(s) url: {url}")
    with urlopen(url, timeout=timeout) as resp:  # noqa: S310 - scheme checked above
        return resp.read()


def _flatten_single_dir(path: Path) -> Path:
    """GitHub tarballs wrap everything in one top dir (repo-hash/); unwrap it."""
    entries = [p for p in path.iterdir() if not p.name.startswith(".")]
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    return path


# --- adapters: link the hub into each tool's skills dir ---------------------
def link_into(hub: Path, tool_dir: str) -> tuple[bool, str]:
    """Point a tool's skills directory at the hub (symlink; back up anything
    real that's already there). Returns (ok, message)."""
    target = Path(os.path.expandvars(os.path.expanduser(tool_dir)))
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink():
            if target.resolve() == hub.resolve():
                return True, f"{target} already linked"
            target.unlink()
        elif target.exists():
            backup = target.with_name(target.name + ".handai-bak")
            if backup.exists():
                shutil.rmtree(backup, ignore_errors=True)
            target.rename(backup)
        target.symlink_to(hub, target_is_directory=True)
        return True, f"linked {target} -> {hub}"
    except OSError as e:
        return False, f"link failed ({target}): {e}"


def remote_targets(providers, modes) -> dict[str, list[tuple[str, str]]]:
    """Map each remote ssh host -> [(provider_label, skills_dir)] of the providers
    that (a) declare a skills_dir and (b) may actually run on that host. Pure /
    duck-typed (works on providers.Provider + Mode) so it's unit-testable.
    """
    out: dict[str, list[tuple[str, str]]] = {}
    for m in modes:
        if not getattr(m, "is_remote", False) or not m.host:
            continue
        for p in providers:
            if p.skills_dir and p.allows_mode(m.id):
                pair = (p.label, p.skills_dir)
                bucket = out.setdefault(m.host, [])
                if pair not in bucket:
                    bucket.append(pair)
    return out
