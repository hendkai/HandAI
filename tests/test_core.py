"""Core tests — no curses, run anywhere (incl. Windows dev box).

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import os
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import io
import tarfile
import zipfile

from handai.config import Config
from handai.network import Network, detect_iface, parse_saved_networks, parse_scan_results
from handai import audio, devices, diagnostics, hardware_report, power, preferences, skill_catalog, skills
from handai.providers import Mode, Provider, parse_modes, parse_providers
from handai.remote import _export_line
from handai.router import _cd_expr, build_target, session_name
from handai.secrets import SecretStore
from handai import tmux
from handai import phone, tailscale
from handai.pixelgui import PixelCockpit, THEMES, load_theme, save_theme, provider_actions, provider_brand, _FONT


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

    def test_gateway_mode_needs_endpoint(self):
        with self.assertRaises(ValueError):
            parse_modes([{"id":"g","transport":"openclaw-gateway"}])
        mode=parse_modes([{"id":"g","transport":"openclaw-gateway","endpoint":"wss://claw.example"}])[0]
        self.assertTrue(mode.is_remote)
        self.assertFalse(mode.is_ssh)

    def test_multiple_auth_methods(self):
        p=parse_providers([{"id":"both","command":["both"],"auth_methods":["oauth-device","token-env"],"token_env":"BOTH_KEY","login_command":["both","login"]}])[0]
        self.assertEqual(p.auth,"oauth-device")
        self.assertTrue(p.supports_auth("oauth-device"))
        self.assertTrue(p.supports_auth("token-env"))

    def test_none_cannot_be_combined(self):
        with self.assertRaises(ValueError):
            parse_providers([{"id":"bad","command":["bad"],"auth_methods":["none","token-env"]}])

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

    def test_ten_unique_themes(self):
        self.assertEqual(len(THEMES), 10)
        self.assertEqual(len({theme.id for theme in THEMES}), 10)
        self.assertEqual(len({theme.bg for theme in THEMES}), 10)

    def test_provider_brands_cover_configured_agents_and_variants(self):
        expected={"claude":"CLAUDE","codex":"CODEX","codex-remote":"CODEX",
                  "hermes":"HERMES","opencode":"OPENCODE","openclaw":"OPENCLAW"}
        self.assertEqual({key:provider_brand(key).wordmark for key in expected},expected)
        self.assertEqual(provider_brand("custom-agent").wordmark,"AI AGENT")

    def test_provider_home_actions_follow_provider_capabilities(self):
        claude=Provider("claude","Claude",["claude"],skills_dir="~/.claude/skills")
        actions=provider_actions(claude,[LOCAL,DEVBOX])
        self.assertIn("REMOTE TARGETS",actions);self.assertIn("TEST CONNECTION",actions)
        self.assertIn("PROVIDER SKILLS",actions);self.assertEqual(actions[-1],"BACK")
        local=provider_actions(Provider("local","Local",["local"]),[LOCAL])
        self.assertNotIn("REMOTE TARGETS",local);self.assertNotIn("PROVIDER SKILLS",local)

    def test_theme_persistence_and_bad_file_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/"ui.json"
            save_theme(THEMES[6],path)
            self.assertEqual(load_theme(path),THEMES[6])
            path.write_text("not json","utf-8")
            self.assertEqual(load_theme(path),THEMES[0])


class TestTailscale(unittest.TestCase):
    def test_parse_status(self):
        raw=json.dumps({"BackendState":"Running","TailscaleIPs":["100.64.1.2"],"Self":{"DNSName":"handai.example.ts.net."}})
        state=tailscale.parse_status(raw)
        self.assertTrue(state.online)
        self.assertEqual(state.ips,("100.64.1.2",))
        self.assertEqual(state.name,"handai.example.ts.net")

    def test_login_url_only_accepts_tailscale(self):
        self.assertEqual(tailscale.parse_login_url("open https://login.tailscale.com/a/abc-123"),"https://login.tailscale.com/a/abc-123")
        self.assertIsNone(tailscale.parse_login_url("https://evil.example/a/abc"))


class TestPhoneKeyboard(unittest.TestCase):
    def test_safe_ip(self):
        self.assertEqual(phone.safe_ip(["::1","100.70.1.2"]),"100.70.1.2")
        self.assertIsNone(phone.safe_ip(["bad","127.0.0.1"]))

    def test_parse_ascii_and_binary_pbm(self):
        self.assertEqual(phone.parse_pbm(b"P1\n2 2\n1 0\n0 1\n"),[[True,False],[False,True]])
        self.assertEqual(phone.parse_pbm(b"P4\n8 1\n\xa0"),[[True,False,True,False,False,False,False,False]])

    @patch("handai.phone.subprocess.run")
    def test_remote_text_is_base64_not_shell_text(self,run):
        run.return_value.returncode=0;run.return_value.stderr=""
        session=tmux.SessionInfo("handai-safe",1,False,"dev@box")
        ok,_=phone.send_session_text(session,"hello; touch /tmp/no",True)
        self.assertTrue(ok)
        command=run.call_args.args[0][-1]
        self.assertNotIn("touch /tmp/no",command)
        self.assertIn("base64 -d",command)

    def test_rejects_oversize_text(self):
        session=tmux.SessionInfo("handai-safe",1,False,None)
        self.assertFalse(phone.send_session_text(session,"x"*(phone.MAX_TEXT+1))[0])


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
        self.assertIn("-t",t.argv)
        self.assertIn("dev@box",t.argv)
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


    def test_openclaw_gateway_runs_local_client_in_tmux(self):
        provider=Provider("openclaw","OpenClaw",["openclaw","tui"])
        mode=Mode("managed-home","Home Claw","openclaw-gateway",endpoint="wss://claw.example")
        target=build_target(provider,mode,"~")
        self.assertEqual(target.argv[0],"tmux")
        self.assertIn("wss://claw.example",target.display)


class TestManagedDevices(unittest.TestCase):
    def test_registry_roundtrip_and_remove(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/"devices.json"
            item=devices.RemoteDevice("desk","Desk","ssh","dev@desk.local","~/src")
            devices.upsert(item,path)
            self.assertEqual(devices.load(path),[item])
            self.assertEqual(devices.remove("desk",path),[])

    def test_address_validation(self):
        self.assertEqual(devices.validate_ssh_host("dev@box.local"),"dev@box.local")
        with self.assertRaises(ValueError):devices.validate_ssh_host("box; reboot")
        self.assertEqual(devices.validate_gateway_url("ws://100.64.1.2:18789"),"ws://100.64.1.2:18789")
        self.assertEqual(devices.validate_gateway_url("wss://claw.example"),"wss://claw.example")
        with self.assertRaises(ValueError):devices.validate_gateway_url("ws://claw.example")
        self.assertEqual(devices.validate_hermes_url("http://100.64.1.3:8642"),"http://100.64.1.3:8642")
        with self.assertRaises(ValueError):devices.validate_hermes_url("http://hermes.example")

    def test_config_loads_managed_targets(self):
        with tempfile.TemporaryDirectory() as d:
            registry=Path(d)/"devices.json"
            devices.save([devices.RemoteDevice("desk","Desk","ssh","dev@desk"),
                          devices.RemoteDevice("claw","Claw","openclaw-gateway","wss://claw.example"),
                          devices.RemoteDevice("hermes","Hermes","hermes-api","https://hermes.example")],registry)
            cfg=Config([Provider("openclaw","OpenClaw",["openclaw"],allowed_modes=["local","devbox"]),
                        Provider("localonly","Local",["local"],allowed_modes=["local"])],
                       [LOCAL,DEVBOX],[])
            cfg.reload_devices(registry)
            ids=[m.id for m in cfg.modes_for(cfg.provider("openclaw"))]
            self.assertIn("managed-desk",ids)
            self.assertIn("managed-claw",ids)
            hermes=Provider("hermes","Hermes",["hermes"],allowed_modes=["devbox"])
            cfg.providers.append(hermes)
            self.assertIn("managed-hermes",[m.id for m in cfg.modes_for(hermes)])
            self.assertNotIn("managed-desk",[m.id for m in cfg.modes_for(cfg.provider("localonly"))])


class TestPreferences(unittest.TestCase):
    def test_first_run_and_button_map_persist(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/"prefs.json"
            self.assertFalse(preferences.completed(path))
            preferences.mark_completed(path)
            preferences.save_button_map({9:"a"},path)
            self.assertTrue(preferences.completed(path))
            self.assertEqual(preferences.button_map(path),{9:"a"})


class TestDiagnostics(unittest.TestCase):
    def test_summary_marks_failures(self):
        ok,lines=diagnostics.summary([diagnostics.Check("one",True,"ready"),diagnostics.Check("two",False,"missing")])
        self.assertFalse(ok);self.assertEqual(lines,["OK one: ready","FAIL two: missing"])


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

    def test_pin_encryption_locks_and_authenticates(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/"secrets.json";store=SecretStore(path);store.set("p","secret")
            store.enable_pin("2468")
            self.assertNotIn('"secret"',path.read_text("utf-8"))
            locked=SecretStore(path);self.assertTrue(locked.locked);self.assertIsNone(locked.get("p"))
            self.assertFalse(locked.unlock("wrong"));self.assertTrue(locked.unlock("2468"))
            self.assertEqual(locked.get("p"),"secret")


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


class TestSkillCatalog(unittest.TestCase):
    def test_parse_public_leaderboard_rows(self):
        row='''<a href="/owner/repo/my-skill"><span>1</span><h3>My Skill</h3><p>owner/repo</p><svg></svg><span class="font-mono text-sm text-foreground">12.3K</span></a>'''
        parsed=skill_catalog.parse_leaderboard(row)
        self.assertEqual(len(parsed),1)
        self.assertEqual(parsed[0].slug,"my-skill")
        self.assertEqual(parsed[0].source,"owner/repo")
        self.assertEqual(parsed[0].installs,"12.3K")

    def test_rejects_unknown_view(self):
        with self.assertRaises(ValueError):skill_catalog.fetch("made-up")


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


class TestVoiceInput(unittest.TestCase):
    def test_pipewire_sources_parse_capture_nodes_only(self):
        raw=json.dumps([
            {"id":42,"info":{"props":{"media.class":"Audio/Source","node.name":"bluez_input.aa","node.description":"Sofa Headset"}}},
            {"id":43,"info":{"props":{"media.class":"Audio/Sink","node.name":"bluez_output.aa","node.description":"Sofa Headset"}}},
        ])
        self.assertEqual(audio.parse_pipewire_dump(raw),[
            audio.AudioSource("bluez_input.aa","Sofa Headset","pipewire")
        ])

    def test_arecord_sources_parse_devices_not_descriptions(self):
        raw="null\n    Discard all samples\ndefault\n    Default\nhw:CARD=USB,DEV=0\n    USB mic\nplughw:CARD=USB,DEV=0\n"
        got=audio.parse_arecord_list(raw)
        self.assertEqual([x.id for x in got],["default","hw:CARD=USB,DEV=0","plughw:CARD=USB,DEV=0"])

    def test_pipewire_sinks_parse_output_nodes(self):
        raw=json.dumps([
            {"id":77,"info":{"props":{"media.class":"Audio/Sink","node.name":"bluez_output.aa",
                                     "node.description":"Sofa Headphones"}}},
            {"id":78,"info":{"props":{"media.class":"Audio/Source","node.name":"bluez_input.aa"}}},
        ])
        self.assertEqual(audio.parse_pipewire_sinks(raw),[
            audio.AudioSink("77","Sofa Headphones","pipewire")
        ])

    def test_volume_parsers_include_mute_state(self):
        self.assertEqual(audio.parse_wpctl_volume("Volume: 0.42 [MUTED]"),
                         audio.VolumeState(42,True,"pipewire"))
        self.assertEqual(audio.parse_amixer_volume("Front Left: 37 [58%] [-20dB] [on]"),
                         audio.VolumeState(58,False,"alsa"))

    def test_generated_tone_has_measurable_signal(self):
        with tempfile.TemporaryDirectory() as d:
            target=audio.make_test_tone(Path(d)/"tone.wav",duration=0.2)
            signal=audio.analyze_wav(target)
            self.assertAlmostEqual(signal.duration,0.2,places=1)
            self.assertGreater(signal.rms_percent,1)
            self.assertFalse(signal.silent)
            self.assertFalse(signal.clipped)

    def test_signal_analysis_detects_silence_and_clipping(self):
        with tempfile.TemporaryDirectory() as d:
            for name,sample in (("silent",0),("clipped",32767)):
                target=Path(d)/f"{name}.wav"
                with wave.open(str(target),"wb") as wav_file:
                    wav_file.setnchannels(1);wav_file.setsampwidth(2);wav_file.setframerate(16000)
                    wav_file.writeframes(struct.pack("<h",sample)*1600)
                signal=audio.analyze_wav(target)
                self.assertEqual(signal.silent,name=="silent")
                self.assertEqual(signal.clipped,name=="clipped")

    def test_record_commands_are_argument_safe(self):
        wav=Path("prompt.wav")
        pw=audio.record_argv(audio.AudioSource("bluez_input.name","Headset","pipewire"),wav)
        alsa=audio.record_argv(audio.AudioSource("hw:CARD=USB,DEV=0","USB","alsa"),wav)
        self.assertIn("--target=bluez_input.name",pw)
        self.assertEqual(alsa[0:4],["arecord","-q","-D","hw:CARD=USB,DEV=0"])

    def test_bluetooth_listing_parser(self):
        text="Device AA:BB:CC:DD:EE:FF Living Room Headset\nController 00:11 nope\n"
        self.assertEqual(audio.parse_bluetooth_devices(text),[
            audio.BluetoothDevice("AA:BB:CC:DD:EE:FF","Living Room Headset")
        ])

    def test_model_checksum_is_pinned(self):
        self.assertEqual(len(audio.MODEL_SHA256),64)
        self.assertTrue(all(c in "0123456789abcdef" for c in audio.MODEL_SHA256))


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


class TestHardwareReport(unittest.TestCase):
    def test_fixture_passes_required_image_and_device_checks(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            files = {
                "sys/firmware/devicetree/base/model": "Anbernic RG35XXSP",
                "sys/class/graphics/fb0/virtual_size": "640,480",
                "sys/class/graphics/fb0/bits_per_pixel": "32",
                "proc/bus/input/devices": "N: Name=gamepad\nH: Handlers=event0",
                "proc/mounts": "/dev/mmcblk0p4 /data ext4 rw 0 0\n",
                "lib/firmware/rtl8821cs.bin": "firmware",
            }
            for name, value in files.items():
                path = root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(value)
            for directory in ("dev/input", "sys/class/net/wlan0/wireless", "data", "lib/modules/4.9.170"):
                (root / directory).mkdir(parents=True, exist_ok=True)
            (root / "dev/input/event0").touch()
            for command in ("handai", "python3", "ssh", "tmux", "tailscale", "tailscaled", "qrencode"):
                path = root / "usr/bin" / command; path.parent.mkdir(parents=True, exist_ok=True); path.touch()
            results = hardware_report.collect(root)
            report = hardware_report.build_report(results)
            self.assertTrue(report["required_ok"])
            self.assertTrue(next(x for x in results if x.name == "wifi").ok)

    def test_required_failure_controls_report_status(self):
        report = hardware_report.build_report([
            hardware_report.Result("display", False, True, "missing"),
            hardware_report.Result("battery", False, False, "missing"),
        ])
        self.assertFalse(report["required_ok"])


class TestPower(unittest.TestCase):
    def test_capabilities_parse_suspend_state(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d) / "state"; state.write_text("freeze mem disk\n")
            with patch("handai.power.os.name", "posix"), patch("handai.power.shutil.which", return_value="/sbin/tool"):
                self.assertEqual(power.capabilities(state), {"shutdown": True, "reboot": True, "suspend": True})

    def test_unknown_action_is_rejected(self):
        self.assertEqual(power.execute("explode"), (False, "unknown power action"))


if __name__ == "__main__":
    unittest.main()
