"""Core tests — no curses, run anywhere (incl. Windows dev box).

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

import io
import tarfile
import zipfile

from handai.config import Config
from handai.network import Network, detect_iface, parse_saved_networks, parse_scan_results
from handai import skills
from handai.providers import Mode, Provider, parse_modes, parse_providers
from handai.remote import _export_line
from handai.router import _cd_expr, build_target, session_name
from handai.secrets import SecretStore
from handai import tmux
from handai.pixelgui import PixelCockpit, _FONT


def _claude():
    return Provider(id="claude", label="Claude", command=["claude"],
                    auth="oauth-device", login_command=["claude", "login"],
                    allowed_modes=["local", "devbox"])


def _hermes():
    return Provider(id="hermes", label="Hermes", command=["hermes", "agent"],
                    auth="token-env", token_env="HERMES_API_KEY",
                    allowed_modes=["devbox"])


LOCAL = Mode(id="local", label="Local", transport="local", default_workdir="~/work")
DEVBOX = Mode(id="devbox", label="Devbox", transport="ssh", host="dev@box",
              default_workdir="~/projects")


class TestProviders(unittest.TestCase):
    def test_parse_roundtrip(self):
        provs = parse_providers([
            {"id": "a", "command": ["a"], "auth": "none"},
            {"id": "b", "label": "B", "command": ["b", "run"],
             "auth": "token-env", "token_env": "B_KEY", "allowed_modes": ["m1"]},
        ])
        self.assertEqual(provs[0].id, "a")
        self.assertEqual(provs[1].token_env, "B_KEY")
        self.assertEqual(provs[1].allowed_modes, ["m1"])

    def test_bad_auth_rejected(self):
        with self.assertRaises(ValueError):
            parse_providers([{"id": "x", "command": ["x"], "auth": "wat"}])

    def test_ssh_mode_needs_host(self):
        with self.assertRaises(ValueError):
            parse_modes([{"id": "r", "transport": "ssh"}])

    def test_allows_mode(self):
        p = _claude()
        self.assertTrue(p.allows_mode("local"))
        self.assertFalse(p.allows_mode("cloud"))
        # empty allowed_modes = all allowed
        self.assertTrue(Provider(id="z", label="Z", command=["z"]).allows_mode("anything"))


class TestPixelGuiPure(unittest.TestCase):
    def test_wrap_keeps_words_and_width(self):
        lines = PixelCockpit.wrap("ONE TWO THREE FOUR", 9)
        self.assertEqual(" ".join(lines), "ONE TWO THREE FOUR")
        self.assertTrue(all(len(line) <= 9 for line in lines))

    def test_font_covers_ui_basics(self):
        for char in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 /._-:@#+[]()!?":
            self.assertIn(char, _FONT)
        self.assertTrue(all(len(glyph) == 7 for glyph in _FONT.values()))


class TestCdExpr(unittest.TestCase):
    def test_tilde_expands_via_home(self):
        self.assertEqual(_cd_expr("~/work/proj"), 'cd "$HOME"/work/proj')

    def test_bare_tilde(self):
        self.assertEqual(_cd_expr("~"), 'cd "$HOME"\'\'')

    def test_absolute(self):
        self.assertEqual(_cd_expr("/workspace/api"), "cd /workspace/api")

    def test_dot_is_noop(self):
        self.assertEqual(_cd_expr("."), "true")

    def test_spaces_quoted(self):
        self.assertEqual(_cd_expr("proj dir/x"), "cd 'proj dir/x'")

    def test_injection_neutralised(self):
        # a workdir trying to inject a command stays a single literal path arg:
        # the whole "; rm -rf /" payload is enclosed in single quotes, so the
        # shell never sees it as a command separator.
        expr = _cd_expr("~/x; rm -rf /")
        self.assertEqual(expr, 'cd "$HOME"\'/x; rm -rf /\'')


class TestRouter(unittest.TestCase):
    def test_local_target(self):
        t = build_target(_claude(), LOCAL, "~/work/proj")
        self.assertEqual(t.argv[:4], ["tmux", "new-session", "-A", "-s"])
        self.assertEqual(t.session, "handai-claude-local-work-proj")
        self.assertIn("claude", t.argv[-1])
        self.assertNotIn("[ -f ~/.handai_env ]", t.argv[-1])  # oauth-device, no source

    def test_remote_target_is_ssh(self):
        t = build_target(_hermes(), DEVBOX, "/workspace/api")
        self.assertEqual(t.argv[0], "ssh")
        self.assertEqual(t.argv[1], "-t")
        self.assertEqual(t.argv[2], "dev@box")
        # remote token-env sources the provisioned env file
        self.assertIn(".handai_env", t.argv[-1])

    def test_mode_guard(self):
        with self.assertRaises(ValueError):
            build_target(_hermes(), LOCAL, "~/x")

    def test_session_name_stable(self):
        a = session_name(_claude(), LOCAL, "~/work/proj")
        b = session_name(_claude(), LOCAL, "~/work/proj")
        self.assertEqual(a, b)
        c = session_name(_claude(), DEVBOX, "~/work/proj")
        self.assertNotEqual(a, c)  # different mode → different session


class TestTmuxParse(unittest.TestCase):
    def test_filters_and_parses(self):
        raw = "handai-claude-local-x\t2\tattached\nother-session\t1\tdetached\n" \
              "handai-hermes-devbox-y\t1\tdetached\n"
        sessions = tmux._parse(raw, host="dev@box")
        names = {s.name for s in sessions}
        self.assertEqual(names, {"handai-claude-local-x", "handai-hermes-devbox-y"})
        s0 = next(s for s in sessions if s.name == "handai-claude-local-x")
        self.assertTrue(s0.attached)
        self.assertEqual(s0.windows, 2)
        self.assertEqual(s0.host, "dev@box")

    def test_attach_argv_local_vs_remote(self):
        from handai.tmux import SessionInfo, attach_argv
        local = SessionInfo("handai-x", 1, False, host=None)
        remote = SessionInfo("handai-y", 1, False, host="dev@box")
        self.assertEqual(attach_argv(local), ["tmux", "attach-session", "-t", "handai-x"])
        self.assertEqual(attach_argv(remote)[0], "ssh")


class TestSecrets(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            store = SecretStore(Path(d) / "secrets.json")
            self.assertFalse(store.has("hermes"))
            store.set("hermes", "tok-123")
            self.assertTrue(store.has("hermes"))
            self.assertEqual(store.get("hermes"), "tok-123")
            # persists to a fresh instance
            store2 = SecretStore(Path(d) / "secrets.json")
            self.assertEqual(store2.get("hermes"), "tok-123")
            store2.clear("hermes")
            self.assertFalse(SecretStore(Path(d) / "secrets.json").has("hermes"))


class TestRemoteShPath(unittest.TestCase):
    def test_tilde_becomes_home(self):
        from handai.remote import _sh_path
        self.assertEqual(_sh_path("~/.claude/skills"), '"$HOME/.claude/skills"')
        self.assertEqual(_sh_path("~"), '"$HOME"')
        self.assertEqual(_sh_path("/abs/dir"), '"/abs/dir"')


class TestRemoteExportLine(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(_export_line("K", "abc"), "export K='abc'\n")

    def test_quote_escaped(self):
        line = _export_line("K", "a'b")
        self.assertEqual(line, "export K='a'\"'\"'b'\n")
        self.assertTrue(line.endswith("\n"))


class TestWifiScanParse(unittest.TestCase):
    SAMPLE = (
        "bssid / frequency / signal level / flags / ssid\n"
        "aa:bb:cc:00:00:01\t2412\t-45\t[WPA2-PSK-CCMP][ESS]\tHomeNet\n"
        "aa:bb:cc:00:00:02\t5200\t-70\t[WPA2-PSK-CCMP][ESS]\tHomeNet\n"  # weaker dup
        "aa:bb:cc:00:00:03\t2437\t-60\t[ESS]\tCafeFree\n"                 # open
        "aa:bb:cc:00:00:04\t2462\t-80\t[WPA2-PSK-CCMP][ESS]\t\n"          # hidden ssid
    )

    def test_parse_dedup_secured_and_sort(self):
        nets = parse_scan_results(self.SAMPLE)
        # hidden SSID dropped, HomeNet deduped to strongest, sorted by signal desc
        self.assertEqual([n.ssid for n in nets], ["HomeNet", "CafeFree"])
        home = nets[0]
        self.assertEqual(home.signal, -45)     # kept the -45, not the -70 dup
        self.assertTrue(home.secured)
        cafe = nets[1]
        self.assertFalse(cafe.secured)          # [ESS] with no WPA/WEP/RSN = open

    def test_empty_and_headeronly(self):
        self.assertEqual(parse_scan_results(""), [])
        self.assertEqual(parse_scan_results("bssid / freq / signal / flags / ssid\n"), [])


class TestIfaceDetect(unittest.TestCase):
    def test_prefers_sys_wireless(self):
        # eth0 first, but wlan0 is the wireless one per the predicate
        got = detect_iface(["eth0", "wlan0", "lo"], is_wireless=lambda n: n == "wlan0")
        self.assertEqual(got, "wlan0")

    def test_name_heuristic_fallback(self):
        # /sys says nothing is wireless → fall back to a wlan-ish name
        got = detect_iface(["eth0", "wlp3s0", "lo"], is_wireless=lambda n: False)
        self.assertEqual(got, "wlp3s0")

    def test_none_when_no_candidate(self):
        self.assertIsNone(detect_iface(["eth0", "lo"], is_wireless=lambda n: False))


class TestSavedNetworksParse(unittest.TestCase):
    SAMPLE = (
        "network id / ssid / bssid / flags\n"
        "0\tHomeNet\tany\t[CURRENT]\n"
        "1\tCafeFree\tany\t\n"
        "\tgarbage line\n"
    )

    def test_parse(self):
        got = parse_saved_networks(self.SAMPLE)
        self.assertEqual(got, [("0", "HomeNet", "[CURRENT]"), ("1", "CafeFree", "")])


class TestSkillsSource(unittest.TestCase):
    def test_github_shorthand(self):
        s = skills.parse_source("acme/cool-skill")
        self.assertEqual(s.kind, "git")
        self.assertEqual(s.location, "https://github.com/acme/cool-skill.git")
        self.assertEqual(s.name, "cool-skill")

    def test_github_shorthand_with_ref(self):
        s = skills.parse_source("acme/cool-skill@v1.2")
        self.assertEqual(s.ref, "v1.2")

    def test_tarball_and_zip(self):
        self.assertEqual(skills.parse_source("https://x.io/foo-1.0.tar.gz").kind, "tar")
        self.assertEqual(skills.parse_source("https://x.io/foo-1.0.tar.gz").name, "foo-1.0")
        self.assertEqual(skills.parse_source("https://x.io/bar.zip").kind, "zip")

    def test_git_url(self):
        self.assertEqual(skills.parse_source("https://gitlab.com/a/b.git").kind, "git")
        self.assertEqual(skills.parse_source("git@github.com:a/b.git").kind, "git")

    def test_unrecognised(self):
        with self.assertRaises(ValueError):
            skills.parse_source("just some words")

    def test_slugify(self):
        self.assertEqual(skills.slugify("My Cool Skill!"), "my-cool-skill")


class TestSkillsSafeExtract(unittest.TestCase):
    def test_tar_traversal_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w") as tf:
                data = b"pwned"
                info = tarfile.TarInfo("../escape.txt")  # path traversal attempt
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))
            buf.seek(0)
            dest = Path(d) / "hub"
            with tarfile.open(fileobj=buf) as tf:
                with self.assertRaises(ValueError):
                    skills.safe_extract_tar(tf, dest)
            self.assertFalse((Path(d) / "escape.txt").exists())

    def test_zip_traversal_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as zf:
                zf.writestr("../escape.txt", "pwned")
            buf.seek(0)
            dest = Path(d) / "hub"
            with zipfile.ZipFile(buf) as zf:
                with self.assertRaises(ValueError):
                    skills.safe_extract_zip(zf, dest)
            self.assertFalse((Path(d) / "escape.txt").exists())

    def test_tar_clean_extracts(self):
        with tempfile.TemporaryDirectory() as d:
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w") as tf:
                data = b"description: a test skill\n"
                info = tarfile.TarInfo("SKILL.md")
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))
            buf.seek(0)
            dest = Path(d) / "hub"
            with tarfile.open(fileobj=buf) as tf:
                skills.safe_extract_tar(tf, dest)
            self.assertTrue((dest / "SKILL.md").exists())


class TestSkillsHubOps(unittest.TestCase):
    def test_list_remove_and_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            hub = Path(d)
            sk = hub / "my-skill"
            sk.mkdir()
            (sk / "SKILL.md").write_text("---\nname: my-skill\ndescription: does things\n---\n", "utf-8")
            (hub / ".hidden").mkdir()  # ignored
            listed = skills.list_installed(hub)
            self.assertEqual([s.name for s in listed], ["my-skill"])
            self.assertEqual(listed[0].description, "does things")
            self.assertTrue(skills.remove(hub, "my-skill"))
            self.assertEqual(skills.list_installed(hub), [])

    def test_remove_rejects_escape(self):
        with tempfile.TemporaryDirectory() as d:
            hub = Path(d) / "hub"
            hub.mkdir()
            self.assertFalse(skills.remove(hub, "../.."))


class TestSkillsRemoteTargets(unittest.TestCase):
    def test_maps_hosts_to_skill_providers(self):
        providers = [
            Provider(id="claude", label="Claude", command=["claude"],
                     allowed_modes=["local", "devbox"], skills_dir="~/.claude/skills"),
            Provider(id="hermes", label="Hermes", command=["hermes"],
                     allowed_modes=["devbox", "cloud"]),  # no skills_dir → excluded
            Provider(id="opencode", label="opencode", command=["opencode"],
                     allowed_modes=["cloud"], skills_dir="~/.config/opencode/skills"),
        ]
        modes = [
            Mode(id="local", label="Local", transport="local"),
            Mode(id="devbox", label="Devbox", transport="ssh", host="dev@box"),
            Mode(id="cloud", label="Cloud", transport="ssh", host="cloud@sb"),
        ]
        got = skills.remote_targets(providers, modes)
        # local mode contributes no host; hermes has no skills_dir
        self.assertEqual(got["dev@box"], [("Claude", "~/.claude/skills")])
        self.assertEqual(got["cloud@sb"], [("opencode", "~/.config/opencode/skills")])
        self.assertNotIn(None, got)


class TestCompose(unittest.TestCase):
    def test_send_keys_argv_literal_and_guarded(self):
        from handai.compose import send_keys_argv, enter_argv
        self.assertEqual(
            send_keys_argv("mysess", "-rf hello"),
            ["tmux", "send-keys", "-t", "mysess", "-l", "--", "-rf hello"],
        )
        self.assertEqual(enter_argv("mysess"), ["tmux", "send-keys", "-t", "mysess", "Enter"])


class TestConfigLoad(unittest.TestCase):
    def test_load_and_env_expand(self):
        cfg_data = {
            "providers": [{"id": "hermes", "command": ["hermes"], "auth": "token-env",
                           "token_env": "HERMES_API_KEY", "allowed_modes": ["cloud"]}],
            "modes": [{"id": "cloud", "transport": "ssh", "host": "${TESTHOST}"}],
            "recent_workdirs": ["~/x"],
        }
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.json"
            p.write_text(json.dumps(cfg_data), "utf-8")
            os.environ["TESTHOST"] = "cloud@sandbox"
            cfg = Config.load(p)
            self.assertEqual(cfg.mode("cloud").host, "cloud@sandbox")
            self.assertEqual([m.id for m in cfg.modes_for(cfg.provider("hermes"))], ["cloud"])


if __name__ == "__main__":
    unittest.main()
