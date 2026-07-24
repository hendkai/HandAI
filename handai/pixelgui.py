"""SDL2 pixel-art cockpit for the 640x480 RG35xxSP display.

The module deliberately uses ctypes instead of pygame/PySDL2: SDL2 is already
part of the image and HandAI's Python package remains dependency-free.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import importlib.util
import json
import os
import select
import shlex
import shutil
import string
import struct
import subprocess
import sys
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence, TypeVar

from . import audio, compose, demo, devices, diagnostics, hardware_report, music, network, oauth, phone, power, preferences, remote, skill_catalog, skills, tailscale, tmux
from .config import Config, config_path
from .providers import Mode, Provider
from .router import build_target
from .secrets import SecretStore

T = TypeVar("T")
OSK_CHARS = string.ascii_uppercase + string.ascii_lowercase + string.digits + " " + string.punctuation

# The H700 kernel exposes the built-in RG35XXSP controls as a generic
# "Deeplay-keys" joystick.  SDL does not classify it as a GameController
# without a mapping, so controller button events would otherwise never reach
# the cockpit.  The physical button/axis ids match the es_input.cfg shipped in
# the pinned KNULLI Gladiator II firmware used by our image builder.
DEEPLAY_CONTROLLER_MAPPING = (
    "19000000010000000100000000010000,Deeplay-keys,"
    "a:b3,b:b4,x:b6,y:b5,back:b9,start:b10,guide:b11,"
    "leftshoulder:b7,rightshoulder:b8,lefttrigger:b13,righttrigger:b14,"
    "leftstick:b12,rightstick:b15,"
    "dpup:h0.1,dpdown:h0.4,dpleft:h0.8,dpright:h0.2,"
    "leftx:a0,lefty:a1,rightx:a2,righty:a3,platform:Linux,"
)


class EvdevInput:
    """Direct fallback for the RG35XXSP's built-in Deeplay-keys device."""

    _EVENT = struct.Struct("@llHHi")
    _KEY_ACTIONS = {
        304: "a",       # A
        305: "b",       # B
        310: "cancel",  # SELECT
        311: "done",    # START
        544: "up",      # BTN_DPAD_UP (alternate kernels)
        545: "down",
        546: "left",
        547: "right",
    }

    def __init__(self, button_map: dict[int, str]):
        self.button_map = button_map
        self.fd: int | None = None
        self.path: Path | None = None
        self.pending: deque[str] = deque()
        self.raw_pending: deque[int] = deque()
        self._open()

    @staticmethod
    def _device_paths() -> list[tuple[Path, str]]:
        found: list[tuple[Path, str]] = []
        for entry in sorted(Path("/sys/class/input").glob("event*")):
            try:
                name = (entry / "device/name").read_text("utf-8").strip()
            except OSError:
                continue
            found.append((Path("/dev/input") / entry.name, name))
        return found

    @staticmethod
    def _is_builtin_name(name: str) -> bool:
        normalized = name.casefold()
        return ("deeplay" in normalized or
                ("anbernic" in normalized and "rg35xx" in normalized and
                 "controller" in normalized))

    def _open(self) -> None:
        devices = self._device_paths()
        selected = next(((path, name) for path, name in devices
                         if self._is_builtin_name(name)), None)
        if not selected:
            names = ", ".join(f"{path.name}:{name}" for path, name in devices) or "none"
            print(f"input: evdev Deeplay-keys not found; devices={names}", file=sys.stderr)
            return
        self.path, name = selected
        try:
            self.fd = os.open(self.path, os.O_RDONLY | os.O_NONBLOCK)
        except OSError as exc:
            print(f"input: evdev open failed path={self.path}: {exc}", file=sys.stderr)
            self.fd = None
            return
        print(f"input: evdev opened path={self.path} name={name!r}", file=sys.stderr)

    def close(self) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def _decode(self, event_type: int, code: int, value: int) -> str | None:
        if event_type == 1 and value == 1:  # EV_KEY, initial press only
            self.raw_pending.append(code)
            return self.button_map.get(code) or self._KEY_ACTIONS.get(code)
        if event_type == 3:  # EV_ABS
            if code == 16 and value:  # ABS_HAT0X
                return "left" if value < 0 else "right"
            if code == 17 and value:  # ABS_HAT0Y
                return "up" if value < 0 else "down"
        return None

    def _read_ready(self) -> None:
        if self.fd is None:
            return
        try:
            payload = os.read(self.fd, self._EVENT.size * 32)
        except BlockingIOError:
            return
        except OSError as exc:
            print(f"input: evdev read failed path={self.path}: {exc}", file=sys.stderr)
            self.close()
            return
        for offset in range(0, len(payload) - self._EVENT.size + 1, self._EVENT.size):
            _, _, event_type, code, value = self._EVENT.unpack_from(payload, offset)
            action = self._decode(event_type, code, value)
            if action:
                self.pending.append(action)

    def poll(self, timeout: float = 0.0) -> str | None:
        if self.pending:
            return self.pending.popleft()
        if self.fd is None:
            return None
        readable, _, _ = select.select([self.fd], [], [], max(0.0, timeout))
        if readable:
            self._read_ready()
        return self.pending.popleft() if self.pending else None

    def raw_button(self) -> int | None:
        while self.fd is not None:
            self.poll(0.1)
            if self.raw_pending:
                return self.raw_pending.popleft()
        return None


@dataclass(frozen=True)
class Theme:
    id: str
    label: str
    bg: tuple[int,int,int]
    panel: tuple[int,int,int]
    panel2: tuple[int,int,int]
    ink: tuple[int,int,int]
    muted: tuple[int,int,int]
    cyan: tuple[int,int,int]
    yellow: tuple[int,int,int]
    pink: tuple[int,int,int]
    green: tuple[int,int,int]


@dataclass(frozen=True)
class ProviderBrand:
    id: str
    wordmark: str
    tagline: str
    accent: tuple[int,int,int]
    deep: tuple[int,int,int]
    mark: str


PROVIDER_BRANDS = {
    "claude": ProviderBrand("claude","CLAUDE","THINK DEEPLY. BUILD CAREFULLY.",(218,119,87),(48,25,20),"spark"),
    "codex": ProviderBrand("codex","CODEX","SHIP CODE FROM ANYWHERE.",(62,207,142),(8,45,38),"code"),
    "hermes": ProviderBrand("hermes","HERMES","FAST AGENTS. SHARED SKILLS.",(245,190,67),(48,31,70),"wings"),
    "opencode": ProviderBrand("opencode","OPENCODE","OPEN TOOLS. OPEN WORKFLOW.",(92,151,255),(19,31,77),"brackets"),
    "openclaw": ProviderBrand("openclaw","OPENCLAW","YOUR CLAW. YOUR MACHINE.",(255,91,91),(72,17,25),"claw"),
}
DEFAULT_PROVIDER_BRAND = ProviderBrand("provider","AI AGENT","LOCAL OR REMOTE. YOU DECIDE.",(50,215,207),(15,35,50),"bot")


def provider_brand(provider_id:str) -> ProviderBrand:
    """Map configured variants such as codex-remote to one visual identity."""
    key=str(provider_id).lower()
    if key.startswith("codex"):key="codex"
    elif key.startswith("claude"):key="claude"
    elif key.startswith("hermes"):key="hermes"
    elif key.startswith("opencode"):key="opencode"
    elif key.startswith("openclaw"):key="openclaw"
    return PROVIDER_BRANDS.get(key,DEFAULT_PROVIDER_BRAND)


def provider_actions(provider:Provider,modes:Sequence[Mode]) -> list[str]:
    remote=any(mode.is_remote for mode in modes)
    actions=["START NEW SESSION","OAUTH / ACCOUNT"]
    if remote:actions.extend(["REMOTE TARGETS","TEST CONNECTION"])
    if provider.skills_dir:actions.append("PROVIDER SKILLS")
    actions.extend(["ACTIVE SESSIONS","ABOUT PROVIDER","BACK"])
    return actions


def openclaw_gateway_health_argv()->list[str]:
    """Use OPENCLAW_GATEWAY_URL/TOKEN so the login token never appears in ps."""
    return ["openclaw","gateway","health","--json"]


THEMES = (
    Theme("neon-night","HANDAI NEXUS",(4,6,22),(13,15,45),(26,26,74),(232,240,255),(127,132,178),(16,222,255),(255,211,68),(255,39,222),(55,255,180)),
    Theme("gameboy","GAME BOY",(15,56,15),(48,98,48),(74,117,62),(155,188,15),(102,137,38),(139,172,15),(190,204,35),(79,123,35),(174,190,49)),
    Theme("amber-crt","AMBER CRT",(20,12,4),(45,27,8),(67,39,10),(255,202,91),(173,113,42),(255,154,24),(255,214,102),(208,85,21),(235,171,54)),
    Theme("arctic","ARCTIC ICE",(7,23,36),(14,45,65),(25,65,88),(226,247,255),(117,167,188),(85,218,255),(235,250,255),(123,151,255),(112,238,203)),
    Theme("synthwave","SYNTHWAVE",(24,8,40),(48,17,68),(72,25,91),(255,232,255),(167,112,183),(40,240,255),(255,220,61),(255,52,175),(89,255,179)),
    Theme("forest","FOREST CAMP",(8,24,18),(18,48,34),(30,68,47),(229,239,207),(125,151,110),(97,210,156),(239,195,100),(219,107,82),(145,207,92)),
    Theme("candy","CANDY POP",(42,22,54),(74,35,77),(105,49,96),(255,239,246),(191,145,183),(83,226,221),(255,216,90),(255,109,177),(135,232,139)),
    Theme("cobalt","COBALT CORE",(5,18,48),(10,38,85),(17,58,117),(224,237,255),(110,150,205),(44,169,255),(255,205,64),(255,95,109),(61,220,166)),
    Theme("lava","LAVA FORGE",(30,8,6),(61,18,11),(91,29,15),(255,232,203),(181,124,92),(255,119,48),(255,205,71),(255,55,36),(158,221,80)),
    Theme("mono","MONOCHROME",(9,9,11),(28,28,31),(47,47,52),(239,239,235),(143,143,143),(210,210,210),(255,255,255),(175,175,175),(225,225,225)),
)
THEME_BY_ID = {theme.id: theme for theme in THEMES}


def theme_path() -> Path:
    state=os.environ.get("HANDAI_STATE") or os.path.expanduser("~/.local/state/handai")
    return Path(os.path.expandvars(os.path.expanduser(state)))/"ui.json"


def load_theme(path:Path|None=None) -> Theme:
    try:
        data=json.loads((path or theme_path()).read_text("utf-8"))
        return THEME_BY_ID.get(str(data.get("theme","")),THEMES[0])
    except (OSError,ValueError,AttributeError):
        return THEMES[0]


def save_theme(theme:Theme,path:Path|None=None) -> None:
    target=path or theme_path(); target.parent.mkdir(parents=True,exist_ok=True)
    tmp=target.with_suffix(".tmp")
    tmp.write_text(json.dumps({"theme":theme.id},indent=2)+"\n","utf-8")
    tmp.replace(target)

# Compact 5x7 font. Lowercase is intentionally rendered as uppercase: that is
# both readable at handheld distance and gives the UI its console-pixel look.
_FONT = {
 " ":("00000",)*7,"A":("01110","10001","10001","11111","10001","10001","10001"),
 "B":("11110","10001","10001","11110","10001","10001","11110"),"C":("01111","10000","10000","10000","10000","10000","01111"),
 "D":("11110","10001","10001","10001","10001","10001","11110"),"E":("11111","10000","10000","11110","10000","10000","11111"),
 "F":("11111","10000","10000","11110","10000","10000","10000"),"G":("01111","10000","10000","10111","10001","10001","01110"),
 "H":("10001","10001","10001","11111","10001","10001","10001"),"I":("11111","00100","00100","00100","00100","00100","11111"),
 "J":("00111","00010","00010","00010","10010","10010","01100"),"K":("10001","10010","10100","11000","10100","10010","10001"),
 "L":("10000","10000","10000","10000","10000","10000","11111"),"M":("10001","11011","10101","10101","10001","10001","10001"),
 "N":("10001","11001","10101","10011","10001","10001","10001"),"O":("01110","10001","10001","10001","10001","10001","01110"),
 "P":("11110","10001","10001","11110","10000","10000","10000"),"Q":("01110","10001","10001","10001","10101","10010","01101"),
 "R":("11110","10001","10001","11110","10100","10010","10001"),"S":("01111","10000","10000","01110","00001","00001","11110"),
 "T":("11111","00100","00100","00100","00100","00100","00100"),"U":("10001","10001","10001","10001","10001","10001","01110"),
 "V":("10001","10001","10001","10001","10001","01010","00100"),"W":("10001","10001","10001","10101","10101","10101","01010"),
 "X":("10001","10001","01010","00100","01010","10001","10001"),"Y":("10001","10001","01010","00100","00100","00100","00100"),
 "Z":("11111","00001","00010","00100","01000","10000","11111"),
 "0":("01110","10001","10011","10101","11001","10001","01110"),"1":("00100","01100","00100","00100","00100","00100","01110"),
 "2":("01110","10001","00001","00010","00100","01000","11111"),"3":("11110","00001","00001","01110","00001","00001","11110"),
 "4":("00010","00110","01010","10010","11111","00010","00010"),"5":("11111","10000","10000","11110","00001","00001","11110"),
 "6":("01110","10000","10000","11110","10001","10001","01110"),"7":("11111","00001","00010","00100","01000","01000","01000"),
 "8":("01110","10001","10001","01110","10001","10001","01110"),"9":("01110","10001","10001","01111","00001","00001","01110"),
 "-":("00000","00000","00000","11111","00000","00000","00000"),"_":("00000","00000","00000","00000","00000","00000","11111"),
 ".":("00000","00000","00000","00000","00000","00110","00110"),",":("00000","00000","00000","00000","00110","00110","00100"),":":("00000","00110","00110","00000","00110","00110","00000"),
 "/":("00001","00010","00010","00100","01000","01000","10000"),"\\":("10000","01000","01000","00100","00010","00010","00001"),
 "?":("01110","10001","00001","00010","00100","00000","00100"),"!":("00100","00100","00100","00100","00100","00000","00100"),
 "+":("00000","00100","00100","11111","00100","00100","00000"),"*":("00000","10101","01110","11111","01110","10101","00000"),
 "[":("01110","01000","01000","01000","01000","01000","01110"),"]":("01110","00010","00010","00010","00010","00010","01110"),
 "(":("00010","00100","01000","01000","01000","00100","00010"),")":("01000","00100","00010","00010","00010","00100","01000"),
 "=":("00000","11111","00000","11111","00000","00000","00000"),"@":("01110","10001","10111","10101","10111","10000","01110"),
 "#":("01010","11111","01010","01010","11111","01010","00000"),"'":("00100","00100","00000","00000","00000","00000","00000"),
 '"':("01010","01010","00000","00000","00000","00000","00000"),"$":("00100","01111","10100","01110","00101","11110","00100"),
 "&":("01100","10010","10100","01000","10101","10010","01101"),";":("00000","00110","00110","00000","00110","00110","00100"),
 "^":("00100","01010","10001","00000","00000","00000","00000"),"`":("01000","00100","00000","00000","00000","00000","00000"),
 "{":("00010","00100","00100","01000","00100","00100","00010"),"|":("00100","00100","00100","00100","00100","00100","00100"),
 "}":("01000","00100","00100","00010","00100","00100","01000"),"~":("00000","00000","01001","10110","00000","00000","00000"),
 "%":("11001","11010","00100","00100","01000","10110","00110"),
 "<":("00010","00100","01000","10000","01000","00100","00010"),">":("01000","00100","00010","00001","00010","00100","01000"),
}

class Rect(ctypes.Structure):
    _fields_ = [("x",ctypes.c_int),("y",ctypes.c_int),("w",ctypes.c_int),("h",ctypes.c_int)]

class SDL:
    W,H=640,480
    BG=(10,14,25); PANEL=(18,27,43); PANEL2=(28,39,57); INK=(224,238,226)
    MUTED=(112,132,145); CYAN=(50,215,207); YELLOW=(250,199,64); PINK=(238,91,137); GREEN=(94,211,118)
    def __init__(self):
        name=ctypes.util.find_library("SDL2")
        # pygame wheels carry the official SDL2 runtime. Using that DLL makes
        # the dependency-free ctypes frontend testable on Windows; pygame is
        # never imported and is not required on the device image.
        if not name and os.name=="nt":
            spec=importlib.util.find_spec("pygame")
            bundled=Path(spec.origin).parent/"SDL2.dll" if spec and spec.origin else None
            name=str(bundled) if bundled and bundled.exists() else "SDL2.dll"
        name=name or "libSDL2-2.0.so.0"
        try: self.s=ctypes.CDLL(name)
        except OSError as e: raise RuntimeError(f"SDL2 unavailable: {e}") from e
        self._bind(); self.window=None; self.renderer=None; self.pad=None; self.evdev=None
        self.button_map=preferences.button_map()
        self.apply_theme(load_theme()); self.open()

    def apply_theme(self,theme:Theme):
        self.theme=theme
        self.BG=theme.bg; self.PANEL=theme.panel; self.PANEL2=theme.panel2
        self.INK=theme.ink; self.MUTED=theme.muted; self.CYAN=theme.cyan
        self.YELLOW=theme.yellow; self.PINK=theme.pink; self.GREEN=theme.green

    def _bind(self):
        s=self.s
        s.SDL_Init.argtypes=[ctypes.c_uint32]; s.SDL_Init.restype=ctypes.c_int
        s.SDL_CreateWindow.argtypes=[ctypes.c_char_p,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_uint32]; s.SDL_CreateWindow.restype=ctypes.c_void_p
        s.SDL_CreateRenderer.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_uint32]; s.SDL_CreateRenderer.restype=ctypes.c_void_p
        s.SDL_RenderSetLogicalSize.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_int]
        s.SDL_SetRenderDrawColor.argtypes=[ctypes.c_void_p,ctypes.c_uint8,ctypes.c_uint8,ctypes.c_uint8,ctypes.c_uint8]
        s.SDL_RenderClear.argtypes=[ctypes.c_void_p]; s.SDL_RenderPresent.argtypes=[ctypes.c_void_p]
        s.SDL_RenderFillRect.argtypes=[ctypes.c_void_p,ctypes.POINTER(Rect)]
        s.SDL_PollEvent.argtypes=[ctypes.c_void_p]; s.SDL_PollEvent.restype=ctypes.c_int
        s.SDL_WaitEvent.argtypes=[ctypes.c_void_p]; s.SDL_WaitEvent.restype=ctypes.c_int
        s.SDL_WaitEventTimeout.argtypes=[ctypes.c_void_p,ctypes.c_int]; s.SDL_WaitEventTimeout.restype=ctypes.c_int
        s.SDL_NumJoysticks.restype=ctypes.c_int; s.SDL_IsGameController.argtypes=[ctypes.c_int]
        s.SDL_JoystickNameForIndex.argtypes=[ctypes.c_int]; s.SDL_JoystickNameForIndex.restype=ctypes.c_char_p
        s.SDL_GameControllerAddMapping.argtypes=[ctypes.c_char_p]; s.SDL_GameControllerAddMapping.restype=ctypes.c_int
        s.SDL_GameControllerOpen.argtypes=[ctypes.c_int]; s.SDL_GameControllerOpen.restype=ctypes.c_void_p
        s.SDL_GameControllerClose.argtypes=[ctypes.c_void_p]
        s.SDL_DestroyRenderer.argtypes=[ctypes.c_void_p]; s.SDL_DestroyWindow.argtypes=[ctypes.c_void_p]
        s.SDL_Quit.argtypes=[]
        s.SDL_GetError.restype=ctypes.c_char_p

    def open(self):
        if self.s.SDL_Init(0x20|0x2000)!=0: raise RuntimeError(self.s.SDL_GetError().decode())
        mapping_result=self.s.SDL_GameControllerAddMapping(DEEPLAY_CONTROLLER_MAPPING.encode())
        if mapping_result<0:
            print(f"input: Deeplay mapping failed: {self.s.SDL_GetError().decode()}",file=sys.stderr)
        fullscreen=0x1001 if os.environ.get("HANDAI_FULLSCREEN", "1" if os.path.exists("/dev/dri") else "0")!="0" else 0x4
        self.window=self.s.SDL_CreateWindow(b"HandAI Pixel Cockpit",0x2FFF0000,0x2FFF0000,self.W,self.H,fullscreen)
        if not self.window: raise RuntimeError(self.s.SDL_GetError().decode())
        self.renderer=self.s.SDL_CreateRenderer(self.window,-1,0x2|0x4) or self.s.SDL_CreateRenderer(self.window,-1,0)
        if not self.renderer: raise RuntimeError(self.s.SDL_GetError().decode())
        self.s.SDL_RenderSetLogicalSize(self.renderer,self.W,self.H)
        joystick_count=self.s.SDL_NumJoysticks()
        print(f"input: SDL joysticks={joystick_count} deeplay_mapping={mapping_result}",file=sys.stderr)
        for i in range(joystick_count):
            raw_name=self.s.SDL_JoystickNameForIndex(i)
            name=raw_name.decode(errors="replace") if raw_name else "unknown"
            is_controller=bool(self.s.SDL_IsGameController(i))
            print(f"input: joystick[{i}] name={name!r} controller={is_controller}",file=sys.stderr)
            if is_controller and not self.pad:
                self.pad=self.s.SDL_GameControllerOpen(i)
        if not self.pad:
            print("input: no SDL GameController opened",file=sys.stderr)
        if os.name == "posix":
            self.evdev=EvdevInput(self.button_map)
        # Explicitly replace the framebuffer boot diagnostic even before the
        # first menu is composed.  This also makes an input wait unmistakable.
        self.clear(); self.present()

    def close(self):
        if self.evdev: self.evdev.close(); self.evdev=None
        if self.pad: self.s.SDL_GameControllerClose(self.pad); self.pad=None
        if self.renderer: self.s.SDL_DestroyRenderer(self.renderer); self.renderer=None
        if self.window: self.s.SDL_DestroyWindow(self.window); self.window=None
        self.s.SDL_Quit()

    def color(self,c): self.s.SDL_SetRenderDrawColor(self.renderer,*c,255)
    def rect(self,x,y,w,h,c):
        self.color(c); r=Rect(x,y,w,h); self.s.SDL_RenderFillRect(self.renderer,ctypes.byref(r))
    def frame(self,x,y,w,h,c,th=3):
        self.rect(x,y,w,th,c); self.rect(x,y+h-th,w,th,c); self.rect(x,y,th,h,c); self.rect(x+w-th,y,th,h,c)
    def text(self,x,y,value,c=None,scale=2,max_chars=None):
        c=c or self.INK; value=str(value); value=value[:max_chars] if max_chars else value
        for ch in value:
            glyph=_FONT.get(ch.upper(),_FONT["?"])
            for gy,row in enumerate(glyph):
                for gx,bit in enumerate(row):
                    if bit=="1": self.rect(x+gx*scale,y+gy*scale,scale,scale,c)
            x+=6*scale
    def clear(self): self.color(self.BG); self.s.SDL_RenderClear(self.renderer)
    def present(self): self.s.SDL_RenderPresent(self.renderer)
    def _decode_event(self,buf):
        typ=int.from_bytes(bytes(buf[0:4]),"little")
        if typ==0x100:return "quit"
        if typ==0x300:
            scan=int.from_bytes(bytes(buf[16:20]),"little",signed=True)
            key=int.from_bytes(bytes(buf[20:24]),"little",signed=True)
            action={1073741906:"up",1073741905:"down",1073741904:"left",1073741903:"right",
                    13:"done",1073741912:"done",32:"a",65:"a",97:"a",66:"b",98:"b",
                    27:"cancel",8:"b",81:"quit",113:"quit"}.get(key)
            return action or {4:"a",5:"b",40:"done",41:"cancel",42:"b",44:"a"}.get(scan,"none")
        if typ==0x651:return self.button_map.get(buf[12],"none")
        return "none"

    def event(self):
        buf=(ctypes.c_uint8*64)()
        while True:
            if self.s.SDL_WaitEventTimeout(ctypes.byref(buf),50):
                action=self._decode_event(buf)
                if action!="none":return action
            if self.evdev:
                action=self.evdev.poll()
                if action:return action

    def event_timeout(self,milliseconds=250):
        """Wait briefly so background login/status screens can keep repainting."""
        buf=(ctypes.c_uint8*64)()
        deadline=time.monotonic()+milliseconds/1000
        while True:
            remaining=deadline-time.monotonic()
            if remaining<=0:return "timeout"
            if self.s.SDL_WaitEventTimeout(ctypes.byref(buf),min(50,max(1,int(remaining*1000)))):
                action=self._decode_event(buf)
                if action!="none":return action
            if self.evdev:
                action=self.evdev.poll()
                if action:return action

    def raw_button(self)->int|None:
        """Wait for one controller button; Escape cancels calibration."""
        if self.evdev and self.evdev.fd is not None:
            return self.evdev.raw_button()
        buf=(ctypes.c_uint8*64)()
        while self.s.SDL_WaitEvent(ctypes.byref(buf)):
            typ=int.from_bytes(bytes(buf[0:4]),"little")
            if typ==0x651:return int(buf[12])
            if typ==0x100:return None
            if typ==0x300:
                key=int.from_bytes(bytes(buf[20:24]),"little",signed=True)
                if key==27:return None
        return None


class PixelCockpit:
    def __init__(self,cfg:Config,secrets:SecretStore,ui:SDL):
        self.cfg=cfg; self.secrets=secrets; self.ui=ui; self.status="SYSTEM READY"
        self.hub=skills.hub_dir(cfg.skills_dir)
        self.music=music.MusicPlayer()

    def chrome(self,title:str,subtitle:str=""):
        u=self.ui; u.clear(); u.rect(0,0,640,58,u.PANEL); u.rect(0,55,640,3,u.CYAN)
        # Tiny pixel bot logo.
        u.rect(20,14,30,30,u.CYAN); u.rect(25,19,20,16,u.BG); u.rect(29,23,4,4,u.YELLOW); u.rect(38,23,4,4,u.YELLOW)
        u.text(64,12,"HANDAI",u.INK,3); u.text(64,38,"PIXEL COCKPIT",u.CYAN,1)
        self.draw_battery()
        short=title[:24]; u.text(max(322,620-len(short)*12),20,short,u.YELLOW,2)
        if subtitle: u.text(22,72,subtitle,u.MUTED,1,max_chars=96)

    def draw_battery(self,x:int=236,y:int=18):
        u=self.ui;state=power.battery_state()
        percent=state.percent
        color=(u.MUTED if percent is None else
               u.GREEN if state.charging else
               u.PINK if percent <= 15 else u.YELLOW)
        u.frame(x,y,30,18,color,2);u.rect(x+30,y+5,4,8,color)
        if percent is not None:
            fill=round(24*percent/100)
            if fill:u.rect(x+3,y+3,fill,12,color)
        prefix="+" if state.status.casefold()=="charging" else "=" if state.status.casefold()=="full" else ""
        label=f"{prefix}{percent}%" if percent is not None else "--%"
        u.text(x+38,y+4,label,color,1,max_chars=8)

    def draw_provider_mark(self,brand:ProviderBrand,x:int,y:int,size:int=92):
        """Draw a chunky original pixel interpretation of the provider identity."""
        u=self.ui;c=brand.accent;d=brand.deep;s=max(4,size//12)
        u.rect(x,y,size,size,d);u.frame(x,y,size,size,c,4)
        cx=x+size//2;cy=y+size//2
        if brand.mark=="spark":
            u.rect(cx-s, y+14,2*s,size-28,c);u.rect(x+14,cy-s,size-28,2*s,c)
            u.rect(cx-3*s,cy-3*s,2*s,2*s,c);u.rect(cx+s,cy+s,2*s,2*s,c)
            u.rect(cx+s,cy-3*s,2*s,2*s,c);u.rect(cx-3*s,cy+s,2*s,2*s,c)
        elif brand.mark=="code":
            u.frame(x+18,y+18,size-36,size-36,c,8);u.rect(x+size-30,cy-10,20,20,d)
            u.rect(cx-5,cy-5,10,10,c)
        elif brand.mark=="wings":
            u.rect(cx-5,y+20,10,size-40,c);u.rect(cx-23,cy-5,46,10,c)
            for i in range(3):
                u.rect(x+10+i*6,y+24+i*8,24-i*6,6,c);u.rect(cx+10,y+24+i*8,24-i*6,6,c)
        elif brand.mark=="brackets":
            u.rect(x+18,y+22,8,size-44,c);u.rect(x+18,y+22,24,8,c);u.rect(x+18,y+size-30,24,8,c)
            u.rect(x+size-26,y+22,8,size-44,c);u.rect(x+size-42,y+22,24,8,c);u.rect(x+size-42,y+size-30,24,8,c)
        elif brand.mark=="claw":
            for i,h in enumerate((40,55,46)):
                px=x+24+i*18;u.rect(px,y+18,8,h,c);u.rect(px-6,y+18,14,8,c)
            u.rect(x+22,y+68,size-44,10,c);u.rect(x+30,y+76,size-60,7,c)
        else:
            u.rect(x+18,y+24,size-36,size-44,c);u.rect(x+27,y+34,size-54,size-62,d)
            u.rect(x+34,y+42,8,8,c);u.rect(x+size-42,y+42,8,8,c);u.rect(cx-4,y+10,8,14,c)

    def provider_menu(self,p:Provider,items:Sequence[str]) -> str|None:
        """Fullscreen branded provider home with a scrollable action menu."""
        brand=provider_brand(p.id);idx=0;top=0;rows=5
        while True:
            if idx<top:top=idx
            if idx>=top+rows:top=idx-rows+1
            u=self.ui;u.clear();u.rect(0,0,640,480,brand.deep)
            # oversize pixel bands make the entire screen feel provider-owned.
            for i in range(0,640,32):u.rect(i,0,16,7,brand.accent)
            self.draw_battery(548,16)
            self.draw_provider_mark(brand,34,42,126)
            word=brand.wordmark[:15];scale=5 if len(word)<=9 else 4
            u.text(188,60,word,brand.accent,scale,max_chars=15)
            u.text(190,112,brand.tagline,u.INK,1,max_chars=68)
            u.text(190,137,"PROVIDER HOME",u.MUTED,2,max_chars=28)
            u.rect(22,190,596,4,brand.accent)
            for row,item in enumerate(items[top:top+rows]):
                y=211+row*43;selected=(top+row)==idx
                u.rect(34,y,572,35,u.PANEL2 if selected else u.PANEL)
                if selected:u.rect(34,y,8,35,brand.accent)
                u.text(54,y+10,item,brand.accent if selected else u.INK,2,max_chars=44)
            u.rect(0,442,640,38,u.PANEL);u.rect(0,442,640,3,brand.accent)
            u.text(18,454,"D-PAD MOVE   A OPEN   B PROVIDERS",u.INK,1,max_chars=98);u.present();e=u.event()
            if e=="up":idx=(idx-1)%len(items)
            elif e=="down":idx=(idx+1)%len(items)
            elif e in ("a","done"):return items[idx]
            elif e in ("b","cancel","quit"):return None

    def footer(self,hint="D-PAD MOVE   A SELECT   B BACK"):
        u=self.ui; u.rect(0,442,640,38,u.PANEL); u.rect(0,442,640,3,u.PINK); u.text(18,454,hint,u.INK,1,max_chars=98)

    def pick(self,title:str,items:Sequence[T],render:Callable[[T],str]=str,subtitle="") -> T|None:
        if not items: self.toast("NOTHING TO CHOOSE"); return None
        idx=0; top=0; rows=8
        while True:
            if idx<top: top=idx
            if idx>=top+rows: top=idx-rows+1
            self.chrome(title,subtitle)
            for row,it in enumerate(items[top:top+rows]):
                y=94+row*40; selected=(top+row)==idx
                self.ui.rect(20,y,600,34,self.ui.PANEL2 if selected else self.ui.PANEL)
                if selected: self.ui.rect(20,y,7,34,self.ui.CYAN); col=self.ui.YELLOW
                else: col=self.ui.INK
                self.ui.text(38,y+10,render(it),col,2,max_chars=47)
            self.footer(); self.ui.present(); e=self.ui.event()
            if e=="up": idx=(idx-1)%len(items)
            elif e=="down": idx=(idx+1)%len(items)
            elif e in ("a","done"): return items[idx]
            elif e in ("b","cancel","quit"): return None

    def toast(self,msg:str,lines:Sequence[str]=()):
        self.chrome("MESSAGE"); self.ui.frame(28,105,584,250,self.ui.CYAN,4)
        wrapped=self.wrap(msg,44)+list(lines)
        for i,line in enumerate(wrapped[:10]): self.ui.text(52,135+i*22,line,self.ui.YELLOW if i==0 else self.ui.INK,2,max_chars=42)
        self.footer("A / B  CONTINUE"); self.ui.present(); self.ui.event()

    @staticmethod
    def wrap(text:str,width:int)->list[str]:
        out=[]; line=""
        words=[]
        for raw in str(text).split():
            words.extend(raw[i:i+width] for i in range(0,len(raw),width))
        for word in words:
            if len(line)+len(word)+1>width: out.append(line); line=word
            else: line=(line+" "+word).strip()
        if line: out.append(line)
        return out or [""]

    def prompt(self,title:str,initial="",secret=False)->str|None:
        chars=list(OSK_CHARS)
        cols=16; value=initial; pos=0
        while True:
            self.chrome(title,"ON-SCREEN KEYBOARD")
            shown="*"*len(value) if secret else value
            self.ui.rect(20,86,600,42,self.ui.PANEL2); self.ui.text(34,100,shown[-47:],self.ui.YELLOW,2,max_chars=47)
            for i,ch in enumerate(chars):
                x=20+(i%cols)*38; y=145+(i//cols)*41
                self.ui.rect(x,y,32,34,self.ui.CYAN if i==pos else self.ui.PANEL)
                label="SP" if ch==" " else ch
                scale=1 if ch==" " else 2
                self.ui.text(x+(9 if ch==" " else 10),y+10,label,
                             self.ui.BG if i==pos else self.ui.INK,scale)
            self.footer("D-PAD MOVE  A TYPE  B DELETE  START/ENTER DONE  ESC CANCEL"); self.ui.present(); e=self.ui.event()
            if e=="left": pos=(pos-1)%len(chars)
            elif e=="right": pos=(pos+1)%len(chars)
            elif e=="up": pos=(pos-cols)%len(chars)
            elif e=="down": pos=(pos+cols)%len(chars)
            elif e=="a": value+=chars[pos]
            elif e=="b":
                if value: value=value[:-1]
                else: return None
            elif e=="done": return value
            elif e in ("cancel","quit"): return None

    def interactive(self,argv:list[str]):
        self.music.pause()
        self.ui.close()
        try: subprocess.call(argv)
        except OSError as e: self.status=f"LAUNCH FAILED: {e}"
        finally:self.ui.open();self.music.resume()

    def env(self,p:Provider):
        if p.supports_auth("token-env") and p.token_env:
            token=self.secrets.get(p.id)
            if token: os.environ[p.token_env]=token
        for k,v in p.env.items(): os.environ.setdefault(k,v)
        os.environ["HANDAI_SKILLS"]=str(self.hub)

    def oauth_login(self,p:Provider,host:str|None=None):
        profiles=p.oauth_profiles
        if not profiles:
            self.toast("NO NATIVE OAUTH PROFILE CONFIGURED");return
        profile=(profiles[0] if len(profiles)==1 else
                 self.pick(f"{provider_brand(p.id).wordmark} OAUTH",profiles,
                           lambda item:item.label,
                           subtitle="CHOOSE SUBSCRIPTION / ACCOUNT PROVIDER"))
        if not profile:return
        argv=(profile.command if not host else
              remote.ssh_argv(
                  host,
                  shlex.join(profile.command),
                  batch=True,
                  tty=profile.requires_tty,
              ))
        session=oauth.LoginSession(
            argv,initial_input=profile.initial_input,requires_tty=profile.requires_tty
        ).start()
        qr_url=None;matrix=None;completion_ack=False
        self.music.pause()
        try:
            while True:
                snap=session.snapshot()
                if snap.state=="completing" and not completion_ack:
                    session.send("");completion_ack=True
                if snap.url and snap.url!=qr_url:
                    qr_url=snap.url
                    try:matrix=phone.qr_matrix(qr_url)
                    except RuntimeError:matrix=None
                u=self.ui
                self.chrome(f"{provider_brand(p.id).wordmark} LOGIN",
                            f"{profile.label}  @  {host or 'THIS HANDHELD'}")
                if matrix:
                    size=len(matrix);scale=max(1,min(6,220//size));span=size*scale;x=28;y=111
                    u.rect(x-7,y-7,span+14,span+14,(255,255,255))
                    for row,bits in enumerate(matrix):
                        for col,on in enumerate(bits):
                            if on:u.rect(x+col*scale,y+row*scale,scale,scale,(0,0,0))
                    tx=286;u.text(tx,112,"SCAN WITH PHONE",u.CYAN,2,max_chars=27)
                    if snap.code:
                        u.text(tx,153,"DEVICE CODE",u.MUTED,1,max_chars=32)
                        u.text(tx,174,snap.code,u.YELLOW,3,max_chars=18)
                    u.text(tx,226,"LOGIN URL",u.MUTED,1,max_chars=32)
                    for i,line in enumerate(self.wrap(qr_url or "",28)[:4]):
                        u.text(tx,244+i*17,line,u.INK,1,max_chars=30)
                else:
                    u.frame(74,112,492,128,u.CYAN,4)
                    u.text(119,145,"STARTING SECURE LOGIN",u.YELLOW,3,max_chars=30)
                    u.text(155,200,"WAITING FOR PROVIDER URL",u.MUTED,2,max_chars=34)
                state_text={"starting":"STARTING","waiting":"WAITING FOR PHONE",
                            "completing":"FINISHING LOGIN","success":"LOGIN COMPLETE",
                            "failed":"LOGIN FAILED","cancelled":"CANCELLED"}.get(snap.state,snap.state.upper())
                u.text(28,337,state_text,u.GREEN if snap.state=="success" else u.PINK if snap.state=="failed" else u.CYAN,2,max_chars=42)
                for i,line in enumerate(oauth.display_lines(snap.output,3,70)):
                    u.text(28,365+i*17,line,u.MUTED,1,max_chars=93)
                self.footer("SCAN QR  |  A ENTER RETURNED CODE  |  B CANCEL")
                u.present()
                if snap.done:
                    break
                event=u.event_timeout(250)
                if event in ("b","cancel","quit"):
                    session.cancel();break
                if event in ("a","done"):
                    value=self.prompt("PASTE LOGIN CODE")
                    if value:session.send(value)
            final=session.wait(2)
            if final.state=="success":
                self.status=f"{p.label} OAUTH READY"+(f" ON {host}" if host else "")
                self.toast("OAUTH LOGIN COMPLETE",[
                    f"PROVIDER: {p.label}",f"ACCOUNT: {profile.label}",
                    f"TARGET: {host or 'THIS HANDHELD'}",
                    "CREDENTIALS WERE STORED BY THE PROVIDER CLI.",
                ])
            elif final.state!="cancelled":
                detail=oauth.display_lines(final.output,7,42)
                self.toast("OAUTH LOGIN FAILED",detail or ["PROVIDER CLI RETURNED AN ERROR."])
        finally:
            if not session.snapshot().done:session.cancel()
            self.music.resume()

    def api_login(self,p:Provider):
        if not p.token_env:self.toast("NO ACCESS TOKEN VARIABLE CONFIGURED");return
        token=self.prompt(f"{p.label} ACCESS TOKEN",self.secrets.get(p.id) or "",True)
        if token is None:return
        if token:self.secrets.set(p.id,token);self.status=f"ACCESS TOKEN STORED FOR {p.label}"
        else:self.secrets.clear(p.id);self.status=f"ACCESS TOKEN CLEARED FOR {p.label}"

    def new_session(self):
        p=self.pick("NEW / PROVIDER",self.cfg.providers,lambda x:f"{x.label}  [{x.auth}]")
        if p:self.provider_hub(p)

    def start_provider_session(self,p:Provider):
        if p.supports_auth("token-env") and not p.supports_auth("oauth-device") and not self.secrets.has(p.id):
            self.toast("ACCESS CREDENTIAL REQUIRED - OPENING ADVANCED LOGIN"); self.api_login(p)
            if not self.secrets.has(p.id):return
        m=self.pick("NEW / MODE",self.cfg.modes_for(p),lambda x:f"{x.label}  {x.host or 'DEVICE'}")
        if not m:return
        if m.transport=="openclaw-gateway":
            os.environ["OPENCLAW_GATEWAY_URL"]=m.endpoint or ""
            gateway_token=self.secrets.get("gateway:"+m.id)
            if gateway_token:os.environ["OPENCLAW_GATEWAY_TOKEN"]=gateway_token
            else:os.environ.pop("OPENCLAW_GATEWAY_TOKEN",None)
        elif m.transport=="hermes-api":
            os.environ["HERMES_REMOTE_URL"]=m.endpoint or ""
            remote_key=self.secrets.get("gateway:"+m.id)
            if remote_key:os.environ["HERMES_REMOTE_TOKEN"]=remote_key
            else:os.environ.pop("HERMES_REMOTE_TOKEN",None)
        choices=list(self.cfg.recent_workdirs)
        if m.default_workdir and m.default_workdir not in choices: choices.insert(0,m.default_workdir)
        choices.append("<ENTER PATH>"); wd=self.pick("NEW / WORKDIR",choices)
        if wd=="<ENTER PATH>": wd=self.prompt("PATH ON TARGET",m.default_workdir or "~/")
        if wd is None:return
        needs_local_cli=m.transport in ("local","openclaw-gateway")
        if needs_local_cli and not shutil.which(p.command[0]):
            self.toast(f"{p.label.upper()} CLI IS NOT INSTALLED",["USE INSTALL LOCAL AGENTS AFTER WIFI IS CONNECTED.","REMOTE PROVIDERS CAN RUN ON YOUR COMPUTER."])
            return
        try: self.env(p); target=build_target(p,m,wd)
        except ValueError as e:self.toast(str(e));return
        self.session_console(target)

    def session_console(self,target):
        self.draw_busy(f"STARTING {target.provider.label}")
        ok,detail=tmux.start_target(target)
        if not ok:self.toast("SESSION START FAILED",[detail]);return
        session=tmux.from_target(target);self.status=f"SESSION READY: {target.provider.label}"
        while True:
            self.chrome(target.provider.label,target.display)
            self.ui.frame(18,88,604,322,self.ui.CYAN,3)
            output=[]
            for line in tmux.capture(session,18):
                output.extend(self.wrap(line,92))
            for row,line in enumerate(output[-13:]):
                color=self.ui.YELLOW if line.lstrip().startswith((">", "YOU", "RESULT")) else self.ui.INK
                self.ui.text(32,105+row*22,line,color,1,max_chars=94)
            self.footer("A TYPE PROMPT   START PHONE QR   B BACK");self.ui.present()
            event=self.ui.event()
            if event=="a":
                prompt=self.prompt(f"PROMPT / {target.provider.label}")
                if prompt:
                    sent,message=tmux.send_text(session,prompt);self.status=message
                    if not sent:self.toast("PROMPT SEND FAILED",[message])
                    else:time.sleep(0.2)
            elif event=="done":
                self.share_phone_keyboard(session)
            elif event in ("b","cancel","quit"):
                self.status=f"{target.provider.label} SESSION RUNNING"
                return

    def provider_hub(self,p:Provider):
        modes=self.cfg.modes_for(p);remote_modes=[m for m in modes if m.is_remote]
        actions=provider_actions(p,modes)
        self.music.play(music.theme_for_provider(p.id))
        try:
            while True:
                act=self.provider_menu(p,actions)
                if act in (None,"BACK"):return
                if act=="START NEW SESSION":self.start_provider_session(p)
                elif act=="OAUTH / ACCOUNT":self.provider_account(p,modes)
                elif act=="REMOTE TARGETS":self.provider_targets(p,remote_modes,False)
                elif act=="TEST CONNECTION":self.provider_targets(p,remote_modes,True)
                elif act=="PROVIDER SKILLS":self.provider_skills(p,remote_modes)
                elif act=="ACTIVE SESSIONS":self.provider_sessions(p)
                elif act=="ABOUT PROVIDER":
                    brand=provider_brand(p.id);local=any(not m.is_remote for m in modes)
                    self.toast(brand.wordmark,[brand.tagline,f"COMMAND: {' '.join(p.command)}",f"LOCAL: {'YES' if local else 'NO'}  REMOTE TARGETS: {len(remote_modes)}",f"AUTH: {' + '.join(p.auth_methods or [p.auth])}","UNOFFICIAL HANDHELD CLIENT UI"])
        finally:self.music.play("main")

    def provider_account(self,p:Provider,modes:Sequence[Mode]):
        targets=[m for m in modes if not m.is_remote or m.is_ssh]
        if not targets:self.provider_login(p,None);return
        mode=self.pick(f"{provider_brand(p.id).wordmark} ACCOUNT",targets,
                       lambda m:f"{m.label} [{m.host or 'THIS HANDHELD'}]",
                       subtitle="CHOOSE WHERE THE PROVIDER CLI RUNS")
        if mode:self.provider_login(p,mode.host if mode.is_ssh else None)

    def provider_targets(self,p:Provider,modes:Sequence[Mode],test:bool):
        mode=self.pick(f"{provider_brand(p.id).wordmark} TARGETS",modes,
                       lambda m:f"{m.label} [{m.host or m.endpoint or m.transport}]",
                       subtitle="TEST A TARGET" if test else "CONFIGURED REMOTE EXECUTION TARGETS")
        if not mode:return
        if test and mode.is_ssh:
            self.draw_busy(f"TESTING {mode.host}");ok,msg=remote.diagnose(mode.host or "")
            self.toast(("READY: " if ok else "NOT READY: ")+msg)
        elif test:
            device_id=mode.id.removeprefix("managed-")
            self.test_gateway(devices.RemoteDevice(
                device_id,mode.label,mode.transport,mode.endpoint or "",
                mode.default_workdir or "~",
            ))
        else:self.toast(mode.label,[f"TRANSPORT: {mode.transport}",f"ADDRESS: {mode.host or mode.endpoint or '-'}",f"WORKDIR: {mode.default_workdir or '-'}"])

    def provider_skills(self,p:Provider,remote_modes:Sequence[Mode]):
        targets=["SYNC ON THIS HANDHELD"]
        targets.extend(f"SYNC TO {m.label}" for m in remote_modes if m.is_ssh)
        choice=self.pick(f"{provider_brand(p.id).wordmark} SKILLS",targets,subtitle=f"SHARED HUB -> {p.skills_dir}")
        if choice=="SYNC ON THIS HANDHELD":
            ok,msg=skills.link_into(self.hub,p.skills_dir or "");self.toast("SKILLS LINKED" if ok else "SKILL LINK FAILED",[msg])
        elif choice:
            label=choice.removeprefix("SYNC TO ");mode=next((m for m in remote_modes if m.label==label and m.is_ssh),None)
            if mode:
                self.draw_busy(f"SYNCING TO {mode.host}");ok,msg=remote.sync_hub(mode.host or "",self.hub)
                if ok:ok,msg=remote.link_remote(mode.host or "",p.skills_dir or "")
                self.toast("REMOTE SKILLS LINKED" if ok else "REMOTE SKILL SYNC FAILED",[msg])

    def provider_sessions(self,p:Provider):
        self.draw_busy(f"SCANNING {p.label}")
        prefix=f"handai-{p.id}-";found=[s for s in tmux.list_all(self.cfg.modes) if s.name.startswith(prefix)]
        session=self.pick(f"{provider_brand(p.id).wordmark} SESSIONS",found,lambda s:f"{'*' if s.attached else 'O'} {s.name} [{s.host or 'DEVICE'}]")
        if not session:return
        act=self.pick(session.name,["OPEN PIXEL CONSOLE","TERMINAL ATTACH","KILL SESSION"])
        if act=="OPEN PIXEL CONSOLE":self.session_viewer(session,p.label)
        elif act=="TERMINAL ATTACH":self.interactive(tmux.attach_argv(session))
        elif act=="KILL SESSION":self.toast("SESSION KILLED" if tmux.kill(session) else "KILL FAILED")

    def sessions(self):
        self.draw_busy("SCANNING SESSIONS")
        found=tmux.list_all(self.cfg.modes)
        s=self.pick("SESSIONS",found,lambda x:f"{'*' if x.attached else 'O'} {x.name} [{x.host or 'DEVICE'}]")
        if not s:return
        act=self.pick(s.name,["OPEN PIXEL CONSOLE","TERMINAL ATTACH","KILL SESSION"])
        if act=="OPEN PIXEL CONSOLE":self.session_viewer(s,s.name)
        elif act=="TERMINAL ATTACH":self.interactive(tmux.attach_argv(s));self.status=f"DETACHED FROM {s.name}"
        elif act=="KILL SESSION":self.status=f"KILLED {s.name}" if tmux.kill(s) else f"KILL FAILED: {s.name}"

    def session_viewer(self,session,label):
        while True:
            self.chrome("ACTIVE SESSION",label)
            self.ui.frame(18,88,604,322,self.ui.CYAN,3);output=[]
            for line in tmux.capture(session,18):output.extend(self.wrap(line,92))
            for row,line in enumerate(output[-13:]):self.ui.text(32,105+row*22,line,self.ui.INK,1,max_chars=94)
            self.footer("A TYPE PROMPT   START PHONE QR   B BACK");self.ui.present();event=self.ui.event()
            if event=="a":
                value=self.prompt("SESSION PROMPT")
                if value:
                    ok,message=tmux.send_text(session,value);self.status=message
                    if not ok:self.toast("PROMPT SEND FAILED",[message])
                    else:time.sleep(0.2)
            elif event=="done":self.share_phone_keyboard(session)
            elif event in ("b","cancel","quit"):return

    def providers(self):
        area=self.pick("PROVIDERS / LOGIN",["LOCAL PROVIDERS","REMOTE PROVIDERS"])
        if area=="LOCAL PROVIDERS":
            local=next((m for m in self.cfg.modes if not m.is_remote),None)
            candidates=[p for p in self.cfg.providers if local and p.allows_mode(local.id)]
            p=self.pick("LOCAL PROVIDERS",candidates,self.local_provider_label,subtitle="RUNS ON THIS HANDHELD")
            if p:self.provider_hub(p)
        elif area=="REMOTE PROVIDERS":
            remote_modes=[m for m in self.cfg.modes if m.is_remote]
            candidates=[p for p in self.cfg.providers
                        if any(m in self.cfg.modes_for(p) for m in remote_modes)]
            choices=["ADD REMOTE TARGET",*candidates]
            choice=self.pick("REMOTE PROVIDERS",choices,
                             lambda p:p if isinstance(p,str) else
                             f"? {p.label} [CHOOSE TARGET]",
                             subtitle="RUN THE AGENT ON YOUR COMPUTER OR SERVER")
            if choice=="ADD REMOTE TARGET":self.remote_devices()
            elif choice:self.provider_hub(choice)

    def provider_label(self,p):
        return f"{p.label} [{' + '.join(p.auth_methods or [p.auth])}]"

    def local_provider_label(self,p):
        installed=bool(p.command and shutil.which(p.command[0]))
        return f"{'+' if installed else 'X'} {p.label} [{'INSTALLED' if installed else 'CLI MISSING'}]"

    def provider_login(self,p,host):
        if not p:return
        if p.supports_auth("none"):self.toast("NO AUTHENTICATION NEEDED");return
        acts=[]
        if p.supports_auth("oauth-device"):
            acts.append("OAUTH LOGIN ON DEVICE" if not host else "OAUTH LOGIN ON REMOTE")
        if p.supports_auth("token-env"):
            acts.append("ADVANCED: ENTER ACCESS TOKEN")
            if self.secrets.has(p.id):
                if host:acts.append("ADVANCED: SEND TOKEN TO REMOTE")
                acts.append("ADVANCED: CLEAR ACCESS TOKEN")
        act=self.pick(p.label,acts,subtitle=f"TARGET: {host or 'THIS HANDHELD'}")
        if act=="OAUTH LOGIN ON DEVICE":self.oauth_login(p)
        elif act=="OAUTH LOGIN ON REMOTE":self.oauth_login(p,host)
        elif act=="ADVANCED: ENTER ACCESS TOKEN":self.api_login(p)
        elif act=="ADVANCED: CLEAR ACCESS TOKEN":self.secrets.clear(p.id);self.status=f"ACCESS TOKEN CLEARED FOR {p.label}"
        elif act=="ADVANCED: SEND TOKEN TO REMOTE" and host:
            self.draw_busy(f"PUSHING TO {host}");_,msg=remote.push_token(host,p.token_env or "",self.secrets.get(p.id) or "");self.status=msg;self.toast(msg)

    def network(self):
        act=self.pick("NETWORK",["SCAN AND CONNECT","SAVED NETWORKS","WIFI STATUS","TAILSCALE"],subtitle=network.status())
        if act in ("SCAN AND CONNECT","SAVED NETWORKS","WIFI STATUS") and not network.available():self.toast("WIFI CONTROL UNAVAILABLE - NO WPA_CLI");return
        if act=="WIFI STATUS":self.toast(network.status())
        elif act=="SCAN AND CONNECT":
            self.draw_busy("SCANNING WIFI"); nets=network.scan()
            if not nets:self.toast(network.scan_error() or "NO WIFI NETWORKS FOUND");return
            n=self.pick("WIFI NETWORKS",nets,
                        lambda x:f"{x.security.upper():10} {x.ssid} {x.signal} DBM")
            if not n:return
            if n.security=="enterprise":
                self.toast("ENTERPRISE WIFI NEEDS A PRECONFIGURED PROFILE",[
                    "802.1X USER/CERTIFICATE SETUP IS NOT SAFE TO GUESS.",
                    "ADD IT TO WPA_SUPPLICANT.CONF, THEN USE SAVED NETWORKS.",
                ]);return
            psk=self.prompt(f"PASSWORD / {n.ssid}",secret=True) if n.secured else None
            if n.secured and psk is None:return
            self.draw_busy(f"CONNECTING {n.ssid}");ok=network.connect(n.ssid,psk,security=n.security);self.status=network.status();self.toast("CONNECTED" if ok else "CONNECTION FAILED")
        elif act=="SAVED NETWORKS":
            ssid=self.pick("SAVED NETWORKS",network.saved())
            if not ssid:return
            sub=self.pick(ssid,["RECONNECT","FORGET"])
            if sub=="RECONNECT":self.draw_busy(f"RECONNECTING {ssid}");self.toast("CONNECTED" if network.reconnect(ssid) else "RECONNECT FAILED")
            elif sub=="FORGET":self.toast(f"FORGOT {ssid}" if network.forget(ssid) else "FORGET FAILED")
        elif act=="TAILSCALE":self.tailscale_screen()

    def tailscale_screen(self):
        state=tailscale.status()
        if not state.available:self.toast("TAILSCALE CLI IS NOT INSTALLED");return
        detail=f"{state.state.upper()}  {' '.join(state.ips) or 'NO IP'}"
        acts=["SHOW STATUS","LOGIN WITH PHONE"]
        if state.online:acts.append("LOGOUT")
        act=self.pick("TAILSCALE",acts,subtitle=detail)
        if act=="SHOW STATUS":self.toast("TAILSCALE STATUS",[f"STATE: {state.state}",f"NAME: {state.name or '-'}",f"IPS: {' '.join(state.ips) or '-'}"])
        elif act=="LOGIN WITH PHONE":
            self.draw_busy("REQUESTING LOGIN QR");ok,value=tailscale.login_url()
            if not ok:self.toast(value);return
            if value=="already-online":self.toast("TAILSCALE IS ALREADY ONLINE");return
            self.show_qr("TAILSCALE LOGIN",value,"SCAN WITH PHONE - LOGIN - THEN PRESS B")
            fresh=tailscale.status();self.status=f"TAILSCALE: {fresh.state.upper()}"
        elif act=="LOGOUT" and self.pick("TAILSCALE LOGOUT",["YES LOGOUT","CANCEL"])=="YES LOGOUT":
            ok,msg=tailscale.logout();self.status="TAILSCALE LOGGED OUT" if ok else msg;self.toast(self.status)

    def show_qr(self,title,value,hint):
        try:matrix=phone.qr_matrix(value)
        except RuntimeError as e:self.toast(str(e),[value]);return
        self.chrome(title,value[:78]);size=len(matrix);scale=max(1,min(8,310//size));span=size*scale;x=(640-span)//2;y=104
        self.ui.rect(x-8,y-8,span+16,span+16,(255,255,255))
        for row,bits in enumerate(matrix):
            for col,on in enumerate(bits):
                if on:self.ui.rect(x+col*scale,y+row*scale,scale,scale,(0,0,0))
        self.footer(hint);self.ui.present()
        while self.ui.event() not in ("a","b","done","cancel","quit"):pass

    def phone_keyboard(self):
        self.draw_busy("SCANNING SESSIONS");sessions=tmux.list_all(self.cfg.modes)
        session=self.pick("PHONE KEYBOARD",sessions,lambda x:f"{x.name} [{x.host or 'DEVICE'}]")
        if not session:return
        self.share_phone_keyboard(session)

    def share_phone_keyboard(self,session):
        ts=tailscale.status();host=phone.safe_ip(ts.ips) if ts.online else None
        host=host or phone.local_ip()
        if host=="127.0.0.1":self.toast("NO LAN OR TAILSCALE IP FOUND");return
        bridge=phone.PhoneKeyboard(session,host).start()
        try:
            self.show_qr("PAIR PHONE KEYBOARD",bridge.url,"SCAN QR - TYPE ON PHONE - B STOPS SHARING")
        finally:bridge.stop()
        self.status="PHONE KEYBOARD STOPPED"

    def voice_input(self):
        while True:
            sources=audio.list_sources();source=audio.selected_source(sources)
            model="READY" if audio.model_path().exists() else "NOT INSTALLED"
            acts=[
                "RECORD PROMPT",
                f"INPUT SOURCE: {source.label if source else 'NONE'}",
                "BLUETOOTH HEADSETS",
                f"LOCAL VOICE MODEL: {model}",
                "HOW VOICE INPUT WORKS",
                "BACK",
            ]
            act=self.pick("VOICE INPUT",acts,subtitle="LOCAL SPEECH TO TEXT - NO API KEY")
            if act in (None,"BACK"):return
            if act=="RECORD PROMPT":self.record_voice_prompt(source)
            elif act.startswith("INPUT SOURCE"):self.choose_audio_source(sources)
            elif act=="BLUETOOTH HEADSETS":self.bluetooth_headsets()
            elif act.startswith("LOCAL VOICE MODEL"):
                if audio.model_path().exists():
                    self.toast("LOCAL WHISPER MODEL READY",[str(audio.model_path()),"MULTILINGUAL TINY-Q5 MODEL","NO AUDIO LEAVES THE DEVICE."])
                elif self.pick("INSTALL VOICE MODEL",["DOWNLOAD 31 MB","CANCEL"],subtitle="MULTILINGUAL - CHECKSUM VERIFIED")=="DOWNLOAD 31 MB":
                    self.draw_busy("DOWNLOADING VOICE MODEL")
                    ok,msg=audio.install_model();self.toast(msg if ok else "MODEL INSTALL FAILED",[msg] if not ok else [])
            elif act=="HOW VOICE INPUT WORKS":
                self.toast("PUSH TO TALK",[
                    "CHOOSE A RUNNING AGENT SESSION.",
                    "SPEAK, THEN PRESS A OR B TO STOP.",
                    "WHISPER TRANSCRIBES LOCALLY.",
                    "EDIT THE TEXT, THEN SEND IT.",
                    "BLUETOOTH MIC REQUIRES HFP OR HSP; A2DP IS PLAYBACK ONLY.",
                ])

    def choose_audio_source(self,sources):
        if not sources:
            self.toast("NO MICROPHONE FOUND",["CONNECT USB AUDIO OR PAIR A BLUETOOTH HFP HEADSET.","THEN OPEN INPUT SOURCE AGAIN."]);return
        source=self.pick("MICROPHONE",sources,lambda x:f"{x.label} [{x.backend.upper()}]",subtitle="PIPEWIRE SOURCES INCLUDE BLUETOOTH HFP")
        if source:audio.save_source(source);self.status=f"MICROPHONE: {source.label}"

    def choose_audio_sink(self,sinks):
        if not sinks:
            self.toast("NO AUDIO OUTPUT FOUND",["CONNECT HEADPHONES OR PAIR A BLUETOOTH HEADSET.","THEN OPEN OUTPUT DEVICE AGAIN."]);return
        sink=self.pick("OUTPUT DEVICE",sinks,lambda x:f"{x.label} [{x.backend.upper()}]",
                       subtitle="SPEAKER / WIRED / BLUETOOTH HEADPHONES")
        if sink:
            ok,msg=audio.save_sink(sink);self.status=msg
            if not ok:self.toast("OUTPUT CHANGE FAILED",[msg])

    def adjust_audio_level(self,kind,source=None,sink=None):
        state=audio.get_volume(kind,source,sink)
        title="MIC INPUT LEVEL" if kind=="input" else "OUTPUT VOLUME"
        maximum=100 if kind=="input" else 150
        percent=state.percent;muted=state.muted
        while True:
            u=self.ui;self.chrome(title,f"{state.backend.upper()} MIXER - {'MUTED' if muted else 'ACTIVE'}")
            u.frame(54,137,532,150,u.CYAN,4)
            u.text(88,163,f"{percent:3d}%",u.YELLOW,5,max_chars=5)
            u.rect(88,232,464,28,u.PANEL2)
            width=round(464*min(percent,100)/100)
            if width:u.rect(88,232,width,28,u.PINK if percent>90 else u.GREEN)
            if maximum>100:
                u.text(88,277,"BOOST RANGE: 101-150%",u.MUTED,1,max_chars=35)
            u.text(89,315,"MUTED" if muted else "SOUND ON",u.PINK if muted else u.GREEN,3,max_chars=18)
            self.footer("LEFT/RIGHT 5%  UP/DOWN 10%  A MUTE  B SAVE");u.present()
            event=u.event()
            if event in ("b","cancel","quit"):return
            if event=="left":percent=max(0,percent-5)
            elif event=="right":percent=min(maximum,percent+5)
            elif event=="down":percent=max(0,percent-10)
            elif event=="up":percent=min(maximum,percent+10)
            elif event in ("a","done"):muted=not muted
            else:continue
            ok,msg=audio.set_volume(kind,percent,muted,source,sink)
            if not ok:self.toast("VOLUME CHANGE FAILED",[msg]);return
            state=audio.VolumeState(percent,muted,state.backend)

    def speaker_test(self,sink):
        self.draw_busy("PLAYING SPEAKER / HEADPHONE TONE")
        try:tone=audio.make_test_tone()
        except (OSError,ValueError) as e:self.toast(f"TEST FILE FAILED: {e}");return
        self.music.pause()
        try:ok,msg=audio.play_audio(tone,sink)
        finally:self.music.resume()
        self.toast("OUTPUT TEST COMPLETE" if ok else "OUTPUT TEST FAILED",
                   [msg,f"DEVICE: {sink.label if sink else 'SYSTEM DEFAULT'}",f"FILE: {tone}"])

    def microphone_test(self,source):
        if not source:
            self.toast("NO MICROPHONE SELECTED",["SELECT A MICROPHONE OR PAIR A BLUETOOTH HFP HEADSET."]);return
        wav=audio.state_dir()/"mic-test.wav"
        self.music.pause()
        try:process=audio.start_recording(source,wav)
        except OSError as e:self.music.resume();self.toast(f"MICROPHONE START FAILED: {e}");return
        u=self.ui;self.chrome("MICROPHONE TEST",source.label[:78]);u.frame(42,115,556,235,u.PINK,5)
        u.text(134,145,"SPEAK NOW",u.YELLOW,4,max_chars=20)
        for i,height in enumerate((22,52,96,38,120,68,30,106,56,86,42,112,62,28,76,98)):
            x=70+i*31;u.rect(x,255-height//2,15,height,u.CYAN if i%2 else u.GREEN)
        u.text(102,326,"SAY A FULL TEST SENTENCE",u.INK,2,max_chars=34)
        self.footer("A / B / START  FINISH RECORDING");u.present()
        while u.event() not in ("a","b","done","cancel","quit"):pass
        ok,msg=audio.stop_recording(process)
        self.music.resume()
        if not ok or not wav.exists() or wav.stat().st_size<=44:
            self.toast("MICROPHONE RECORDING FAILED",[msg or "NO AUDIO DATA RECEIVED"]);return
        try:signal=audio.analyze_wav(wav)
        except (OSError,ValueError) as e:self.toast(f"MIC TEST FILE INVALID: {e}");return
        while True:
            if signal.silent:quality="SILENT - CHECK MIC / MUTE / PROFILE"
            elif signal.clipped:quality="CLIPPING - LOWER MIC INPUT LEVEL"
            elif signal.rms_percent<3:quality="VERY QUIET - RAISE MIC INPUT LEVEL"
            else:quality="SIGNAL DETECTED"
            action=self.pick("MIC TEST RESULT",[
                f"STATUS: {quality}",
                f"AVERAGE LEVEL: {signal.rms_percent}%",
                f"PEAK LEVEL: {signal.peak_percent}%",
                f"DURATION: {signal.duration:.1f} SECONDS",
                "PLAY RECORDING",
                "TEST SPEECH RECOGNITION",
                "RECORD AGAIN",
                "BACK",
            ],subtitle=f"RECORDED FILE: {wav.name}")
            if action in (None,"BACK"):return
            if action=="PLAY RECORDING":
                sink=audio.selected_sink();self.draw_busy("PLAYING MIC RECORDING")
                self.music.pause()
                try:played,detail=audio.play_audio(wav,sink)
                finally:self.music.resume()
                self.toast("MIC PLAYBACK COMPLETE" if played else "MIC PLAYBACK FAILED",[detail])
            elif action=="TEST SPEECH RECOGNITION":
                if not audio.whisper_available() or not audio.model_path().exists():
                    self.toast("LOCAL VOICE MODEL NOT READY",["INSTALL IT UNDER VOICE INPUT.","THE LEVEL TEST AND PLAYBACK STILL WORK WITHOUT IT."]);continue
                self.draw_busy("TESTING SPEECH RECOGNITION")
                recognized,text=audio.transcribe(wav)
                self.toast("SPEECH RECOGNIZED" if recognized else "NO SPEECH RECOGNIZED",[text])
            elif action=="RECORD AGAIN":
                self.microphone_test(source);return

    def audio_settings(self):
        while True:
            sources=audio.list_sources();source=audio.selected_source(sources)
            sinks=audio.list_sinks();sink=audio.selected_sink(sinks)
            output=audio.get_volume("output",sink=sink)
            mic=audio.get_volume("input",source=source)
            acts=[
                f"OUTPUT DEVICE: {sink.label if sink else 'SYSTEM DEFAULT'}",
                f"OUTPUT VOLUME: {output.percent}%{' MUTED' if output.muted else ''}",
                f"MICROPHONE: {source.label if source else 'NONE'}",
                f"MIC INPUT LEVEL: {mic.percent}%{' MUTED' if mic.muted else ''}",
                "TEST SPEAKERS / HEADPHONES",
                "TEST MICROPHONE",
                "BLUETOOTH HEADSETS",
                "AUDIO FILES / TEST RECORDINGS",
                "BACK",
            ]
            act=self.pick("AUDIO CENTER",acts,subtitle="OUTPUT, HEADPHONES AND MICROPHONE DIAGNOSTICS")
            if act in (None,"BACK"):return
            if act.startswith("OUTPUT DEVICE"):self.choose_audio_sink(sinks)
            elif act.startswith("OUTPUT VOLUME"):self.adjust_audio_level("output",sink=sink)
            elif act.startswith("MICROPHONE:"):self.choose_audio_source(sources)
            elif act.startswith("MIC INPUT LEVEL"):self.adjust_audio_level("input",source=source)
            elif act=="TEST SPEAKERS / HEADPHONES":self.speaker_test(sink)
            elif act=="TEST MICROPHONE":self.microphone_test(source)
            elif act=="BLUETOOTH HEADSETS":self.bluetooth_headsets()
            elif act=="AUDIO FILES / TEST RECORDINGS":
                folder=audio.state_dir()
                files=sorted(folder.glob("*.wav")) if folder.exists() else []
                if not files:self.toast("NO AUDIO TEST FILES YET",["RUN A SPEAKER OR MICROPHONE TEST FIRST."]);continue
                chosen=self.pick("AUDIO TEST FILES",files,lambda p:f"{p.name}  {p.stat().st_size//1024} KB")
                if chosen:
                    self.music.pause()
                    try:played,detail=audio.play_audio(chosen,sink)
                    finally:self.music.resume()
                    self.toast("PLAYBACK COMPLETE" if played else "PLAYBACK FAILED",[detail])

    def adjust_music_level(self):
        level=music.volume()
        while True:
            u=self.ui;self.chrome("MUSIC VOLUME",music.ALBUM_TITLE)
            u.frame(54,137,532,150,u.CYAN,4);u.text(88,163,f"{level:3d}%",u.YELLOW,5,max_chars=5)
            u.rect(88,232,464,28,u.PANEL2)
            if level:u.rect(88,232,round(464*level/100),28,u.GREEN)
            u.text(89,315,"DIGITAL CHIPTUNE LEVEL",u.CYAN,2,max_chars=30)
            self.footer("LEFT/RIGHT 5%  UP/DOWN 10%  B SAVE");u.present()
            event=u.event()
            if event in ("b","cancel","quit"):return
            if event=="left":level=max(0,level-5)
            elif event=="right":level=min(100,level+5)
            elif event=="down":level=max(0,level-10)
            elif event=="up":level=min(100,level+10)
            else:continue
            music.save_settings(music.enabled(),level);self.music.refresh()

    def music_settings(self):
        previous=self.music.theme or "main"
        try:
            while True:
                on=music.enabled();level=music.volume()
                current=music.TRACK_BY_ID.get(self.music.theme or "main",music.TRACKS[0])
                act=self.pick("CHIPTUNE ALBUM",[
                    f"MUSIC: {'ON' if on else 'OFF'}",
                    f"MUSIC VOLUME: {level}%",
                    f"NOW PLAYING: {current.title}",
                    "ALBUM / CHOOSE TRACK",
                    f"BY {music.ALBUM_ARTIST} / CC0",
                    "BACK",
                ],subtitle=f"{music.ALBUM_ARTIST} / CC0")
                if act in (None,"BACK"):return
                if act.startswith("MUSIC:"):
                    music.save_settings(not on,level);self.music.refresh()
                elif act.startswith("MUSIC VOLUME"):self.adjust_music_level()
                elif act=="ALBUM / CHOOSE TRACK":
                    track=self.pick(music.ALBUM_TITLE,music.TRACKS,
                                    lambda t:f"{t.title} [{t.screen}]",
                                    subtitle=f"ALL MUSIC BY {music.ALBUM_ARTIST} / CC0")
                    if track:self.music.play(track.id);self.status=f"NOW PLAYING: {track.title}"
                elif act.startswith("BY "):
                    self.toast("MUSIC CREDITS",[
                        f"COMPOSER: {music.ALBUM_ARTIST}",
                        f"LICENSE: {music.ALBUM_LICENSE}",
                        f"SOURCE: {music.ALBUM_SOURCE}",
                        "PROVIDER HOMES SWITCH THEMES AUTOMATICALLY.",
                        "FULL SOURCE LIST IS INCLUDED WITH HANDAI.",
                    ])
        finally:self.music.play(previous)

    def bluetooth_headsets(self):
        if not shutil.which("bluetoothctl"):self.toast("BLUETOOTH CONTROL IS NOT INSTALLED");return
        while True:
            self.draw_busy("READING BLUETOOTH DEVICES")
            paired=audio.bluetooth_devices()
            choices=["SCAN AND PAIR",*paired,"BACK"]
            choice=self.pick("BLUETOOTH HEADSETS",choices,
                             lambda x:x if isinstance(x,str) else f"{'+' if x.connected else 'O'} {x.label}",
                             subtitle="PAIR HEADSETS WITH MICROPHONE / HFP")
            if choice in (None,"BACK"):return
            if choice=="SCAN AND PAIR":
                self.draw_busy("SCANNING BLUETOOTH - 8 SECONDS")
                devices_found=audio.bluetooth_devices(scan=True)
                device=self.pick("FOUND DEVICES",devices_found,lambda x:x.label)
                if device:
                    self.draw_busy(f"PAIRING {device.label}")
                    ok,msg=audio.connect_bluetooth(device,pair=True);self.toast(msg if ok else "PAIRING FAILED",[msg] if not ok else [])
            else:
                self.draw_busy(f"CONNECTING {choice.label}")
                ok,msg=audio.connect_bluetooth(choice);self.toast(msg if ok else "CONNECTION FAILED",[msg] if not ok else [])

    def record_voice_prompt(self,source):
        if not source:self.toast("NO MICROPHONE SELECTED",["OPEN INPUT SOURCE OR PAIR A BLUETOOTH HEADSET."]);return
        if not audio.whisper_available():self.toast("LOCAL SPEECH ENGINE IS NOT INSTALLED");return
        if not audio.model_path().exists():self.toast("INSTALL THE LOCAL VOICE MODEL FIRST");return
        self.draw_busy("SCANNING AGENT SESSIONS")
        sessions=tmux.list_all(self.cfg.modes)
        session=self.pick("VOICE TARGET",sessions,lambda x:f"{x.name} [{x.host or 'DEVICE'}]")
        if not session:return
        wav=audio.recording_path()
        self.music.pause()
        try:process=audio.start_recording(source,wav)
        except OSError as e:self.music.resume();self.toast(f"MICROPHONE START FAILED: {e}");return
        u=self.ui;self.chrome("LISTENING",source.label[:78]);u.frame(70,130,500,190,u.PINK,5)
        # Visual feedback without reading from the capture process.
        for i,height in enumerate((20,54,88,44,112,70,36,96,58,24,74,104,48,82,30)):
            x=94+i*29;u.rect(x,225-height//2,12,height,u.CYAN if i%2 else u.YELLOW)
        u.text(160,346,"SPEAK YOUR PROMPT",u.INK,3,max_chars=24)
        self.footer("A / B / START  STOP RECORDING");u.present()
        while u.event() not in ("a","b","done","cancel","quit"):pass
        ok,msg=audio.stop_recording(process)
        self.music.resume()
        if not ok or not wav.exists() or wav.stat().st_size<=44:
            self.toast("RECORDING FAILED",[msg or "NO AUDIO DATA RECEIVED"]);return
        self.draw_busy("TRANSCRIBING LOCALLY")
        ok,text=audio.transcribe(wav)
        if not ok:self.toast("TRANSCRIPTION FAILED",[text]);return
        edited=self.prompt("EDIT TRANSCRIPT",text)
        if not edited:return
        self.draw_busy(f"SENDING TO {session.name}")
        ok,msg=compose.send_text(session.name,edited,enter=True,host=session.host)
        self.status=f"VOICE PROMPT SENT TO {session.name}" if ok else f"VOICE SEND FAILED: {msg}"
        self.toast(self.status)

    def sync_local(self,quiet=False):
        results=[]
        for p in self.cfg.providers:
            if p.skills_dir: ok,_=skills.link_into(self.hub,p.skills_dir);results.append(f"{'+' if ok else 'X'} {p.label}")
        self.status="SKILLS SYNCED: "+", ".join(results)
        if not quiet:self.toast(self.status)

    def skill_screen(self):
        installed=skills.list_installed(self.hub)
        act=self.pick("SKILLS",["TOP / MOST DOWNLOADED","TRENDING SKILLS","HOT RIGHT NOW","ENTER SOURCE MANUALLY",f"INSTALLED SKILLS ({len(installed)})","SYNC TO TOOLS LOCAL","SYNC TO REMOTE HOSTS"])
        views={"TOP / MOST DOWNLOADED":"all-time","TRENDING SKILLS":"trending","HOT RIGHT NOW":"hot"}
        if act in views:self.browse_skills(views[act])
        elif act=="ENTER SOURCE MANUALLY":self.install_skill_manual()
        elif act and act.startswith("INSTALLED"):
            sk=self.pick("INSTALLED SKILLS",installed,lambda x:f"{x.name} - {x.description}")
            if sk and self.pick(sk.name,["REMOVE","CANCEL"])=="REMOVE":self.toast(f"REMOVED {sk.name}" if skills.remove(self.hub,sk.name) else "REMOVE FAILED")
        elif act=="SYNC TO TOOLS LOCAL":self.sync_local()
        elif act=="SYNC TO REMOTE HOSTS":
            targets=skills.remote_targets(self.cfg.providers,self.cfg.modes);hosts=sorted(targets);pick=self.pick("REMOTE SYNC",["ALL HOSTS",*hosts])
            if not pick:return
            chosen=hosts if pick=="ALL HOSTS" else [pick];lines=[]
            for host in chosen:
                self.draw_busy(f"MIRRORING TO {host}");ok,msg=remote.sync_hub(host,self.hub);lines.append(f"{'+' if ok else 'X'} {host}: {msg}")
                if ok:
                    for label,path in targets[host]:lok,_=remote.link_remote(host,path);lines.append(f" {'+' if lok else 'X'} {label}")
            self.toast("REMOTE SKILL SYNC",lines)

    def install_skill_manual(self):
        spec=self.prompt("SKILL SOURCE")
        if not spec:return
        try:src=skills.parse_source(spec)
        except ValueError as e:self.toast(str(e));return
        if self.pick(f"INSTALL {src.name}",["YES DOWNLOAD","CANCEL"])!="YES DOWNLOAD":return
        self.draw_busy(f"INSTALLING {src.name}")
        try:sk=skills.install(self.hub,spec);self.sync_local(True);self.toast(f"INSTALLED {sk.name} AND SYNCED")
        except (ValueError,OSError) as e:self.toast(f"INSTALL FAILED: {e}")

    def browse_skills(self,view):
        page=0
        while True:
            self.draw_busy("LOADING SKILLS.SH")
            try:rows=skill_catalog.fetch(view,page)
            except (OSError,ValueError) as e:self.toast(f"CATALOG UNAVAILABLE: {e}");return
            choices=[*rows,"NEXT PAGE"]
            chosen=self.pick("SKILLS.SH",choices,lambda x:x if isinstance(x,str) else f"#{x.rank+page*30} {x.name} [{x.installs}]",subtitle=f"{view.upper()} - COMMUNITY CONTENT")
            if chosen is None:return
            if chosen=="NEXT PAGE":page+=1;continue
            warning=[f"SOURCE: {chosen.source}",f"INSTALLS: {chosen.installs}","COMMUNITY SKILLS CAN CONTAIN UNSAFE INSTRUCTIONS.","REVIEW THE FILES AFTER INSTALLING."]
            if self.pick(f"INSTALL {chosen.name}",["YES INSTALL","CANCEL"],subtitle=f"{chosen.source} / {chosen.installs}")!="YES INSTALL":continue
            self.draw_busy(f"INSTALLING {chosen.name}")
            try:
                sk=skills.install_catalog(self.hub,chosen.install_url,chosen.slug)
                self.sync_local(True);self.toast(f"INSTALLED {sk.name} AND SYNCED",warning[:2])
            except (ValueError,OSError) as e:self.toast(f"INSTALL FAILED: {e}")
            return

    def settings(self):
        act=self.pick("SETTINGS",["AUDIO / MIC TEST","MUSIC / CHIPTUNE ALBUM","REMOTE DEVICES","SYSTEM DIAGNOSTICS","HARDWARE ACCEPTANCE REPORT","SYSTEM POWER","SECURE CREDENTIALS WITH PIN","GAMEPAD CALIBRATION","RUN SETUP WIZARD","CHOOSE PIXEL SKIN","SYSTEM STATUS"],subtitle=f"ACTIVE SKIN: {self.ui.theme.label}")
        if act=="AUDIO / MIC TEST":self.audio_settings()
        elif act=="MUSIC / CHIPTUNE ALBUM":self.music_settings()
        elif act=="REMOTE DEVICES": self.remote_devices()
        elif act=="SYSTEM DIAGNOSTICS":
            ok,lines=diagnostics.summary();self.toast("ALL CHECKS PASSED" if ok else "HARDWARE CHECKS NEED ATTENTION",lines)
        elif act=="HARDWARE ACCEPTANCE REPORT":
            self.draw_busy("PROBING DEVICE HARDWARE")
            try:
                report=hardware_report.build_report(hardware_report.collect());target=hardware_report.save(report)
                lines=[f"{'+' if row['ok'] else ('X' if row['required'] else '!')} {row['name']}: {row['detail']}" for row in report["checks"]]
                lines.append(f"SAVED: {target}")
                self.toast("HARDWARE PASSED" if report["required_ok"] else "HARDWARE NEEDS ATTENTION",lines)
            except OSError as e:self.toast(f"REPORT FAILED: {e}")
        elif act=="SYSTEM POWER":self.system_power()
        elif act=="SECURE CREDENTIALS WITH PIN":self.secure_credentials()
        elif act=="GAMEPAD CALIBRATION": self.gamepad_calibration()
        elif act=="RUN SETUP WIZARD": self.first_run(force=True)
        elif act=="CHOOSE PIXEL SKIN": self.choose_skin()
        elif act=="SYSTEM STATUS":
            battery=power.battery_state()
            self.toast("SYSTEM STATUS",[power.battery_label(battery),f"POWER SENSOR: {battery.source or 'NOT FOUND'}",f"CONFIG: {config_path()}",f"STATE: {self.secrets.path}",network.status(),f"PROVIDERS: {len(self.cfg.providers)}  MODES: {len(self.cfg.modes)}",f"SKIN: {self.ui.theme.label}","GUI: SDL2 PIXEL / 640X480"])

    def system_power(self):
        caps=power.capabilities();labels=[]
        if caps["suspend"]:labels.append("SUSPEND")
        if caps["reboot"]:labels.append("REBOOT")
        if caps["shutdown"]:labels.append("SHUT DOWN")
        if not labels:self.toast("POWER CONTROL UNAVAILABLE ON THIS SYSTEM");return
        action=self.pick("SYSTEM POWER",labels,subtitle="FILESYSTEMS ARE SYNCED BEFORE THE ACTION")
        if not action:return
        if self.pick(f"CONFIRM {action}",[f"YES {action}","CANCEL"],subtitle="ACTIVE AGENT SESSIONS STAY ON REMOTE HOSTS")!=f"YES {action}":return
        key={"SUSPEND":"suspend","REBOOT":"reboot","SHUT DOWN":"shutdown"}[action]
        self.draw_busy(f"REQUESTING {action}");ok,msg=power.execute(key)
        if not ok:self.toast(f"POWER ACTION FAILED: {msg}")

    def secure_credentials(self):
        pin=self.prompt("NEW BOOT PIN","",True)
        if pin is None:return
        again=self.prompt("REPEAT BOOT PIN","",True)
        if pin!=again:self.toast("PINS DO NOT MATCH");return
        try:self.secrets.enable_pin(pin);self.status="CREDENTIAL STORE ENCRYPTED"
        except ValueError as e:self.toast(str(e))

    def unlock_credentials(self):
        while self.secrets.locked:
            pin=self.prompt("UNLOCK CREDENTIALS","",True)
            if pin is None:self.toast("CREDENTIALS STAY LOCKED");return
            if self.secrets.unlock(pin):self.status="CREDENTIALS UNLOCKED";return
            self.toast("WRONG PIN")

    def gamepad_calibration(self):
        choice=self.pick("GAMEPAD",["CALIBRATE ALL BUTTONS","USE STANDARD SDL MAPPING","INFO"],subtitle="MAP THE PHYSICAL HANDHELD CONTROLS")
        if choice=="CALIBRATE ALL BUTTONS":
            if not self.ui.pad and not (self.ui.evdev and self.ui.evdev.fd is not None):
                self.toast("NO GAME CONTROLLER DETECTED");return
            mapping={}
            for action,label in (("a","SELECT / A"),("b","BACK / B"),("done","START / DONE"),("cancel","MENU / CANCEL"),("up","D-PAD UP"),("down","D-PAD DOWN"),("left","D-PAD LEFT"),("right","D-PAD RIGHT")):
                self.chrome("GAMEPAD CALIBRATION",f"PRESS {label} - ESC CANCELS")
                self.ui.frame(60,150,520,130,self.ui.CYAN,4);self.ui.text(105,205,f"PRESS {label}",self.ui.YELLOW,2,max_chars=35)
                self.footer("PRESS THE REQUESTED PHYSICAL BUTTON");self.ui.present()
                button=self.ui.raw_button()
                if button is None:self.toast("CALIBRATION CANCELLED");return
                mapping[button]=action
            preferences.save_button_map(mapping);self.ui.button_map=mapping
            if self.ui.evdev:self.ui.evdev.button_map=mapping
            self.status="GAMEPAD CALIBRATION SAVED"
        elif choice=="USE STANDARD SDL MAPPING":
            preferences.save_button_map(preferences.DEFAULT_BUTTONS);self.ui.button_map=preferences.button_map()
            if self.ui.evdev:self.ui.evdev.button_map=self.ui.button_map
            self.status="STANDARD GAMEPAD MAP RESTORED"
        elif choice=="INFO":self.toast("RG35XXSP BUILT-IN CONTROLS USE DIRECT LINUX INPUT",["A SELECTS, B GOES BACK, START CONFIRMS.","EXTERNAL CONTROLLERS USE SDL."])

    def first_run(self,force=False):
        if not force and preferences.completed():return
        start=self.pick("WELCOME TO HANDAI",["START GUIDED SETUP","SKIP FOR NOW"],subtitle="WIFI - TAILSCALE - REMOTE COMPUTER - AI LOGIN")
        if start!="START GUIDED SETUP":return
        if self.pick("STEP 1 / NETWORK",["OPEN WIFI SETUP","SKIP"])=="OPEN WIFI SETUP":self.network()
        if self.pick("STEP 2 / TAILSCALE",["LOGIN WITH PHONE","SKIP"])=="LOGIN WITH PHONE":self.tailscale_screen()
        if self.pick("STEP 3 / REMOTE",["ADD COMPUTER OR GATEWAY","SKIP"])=="ADD COMPUTER OR GATEWAY":self.remote_devices()
        if self.pick("STEP 4 / AI ACCOUNT",["OPEN PROVIDER LOGIN","SKIP"])=="OPEN PROVIDER LOGIN":self.providers()
        preferences.mark_completed();self.status="SETUP COMPLETE"
        self.toast("HANDAI IS READY",["START A SESSION, THEN PAIR PHONE KEYBOARD FROM HOME."])

    def remote_devices(self):
        while True:
            saved=devices.load()
            choices=["ADD SSH DEVICE","ADD OPENCLAW GATEWAY","ADD HERMES SERVER",*saved]
            item=self.pick("REMOTE DEVICES",choices,
                           lambda x:x if isinstance(x,str) else f"{x.label} [{x.address}]",
                           subtitle="SSH COMPUTERS AND OPENCLAW GATEWAYS")
            if item is None:return
            if item=="ADD SSH DEVICE":
                label=self.prompt("DEVICE NAME")
                if not label:continue
                address=self.prompt("SSH USER@HOST")
                if not address:continue
                try:address=devices.validate_ssh_host(address)
                except ValueError as e:self.toast(str(e));continue
                wd=self.prompt("DEFAULT WORKDIR","~/projects") or "~/projects"
                dev=devices.RemoteDevice(devices.slug(label),label,"ssh",address,wd)
                devices.upsert(dev);self.cfg.reload_devices();self.setup_ssh(dev)
            elif item=="ADD OPENCLAW GATEWAY":
                label=self.prompt("GATEWAY NAME")
                if not label:continue
                address=self.prompt("WS OR WSS URL","ws://100.")
                if not address:continue
                try:address=devices.validate_gateway_url(address)
                except ValueError as e:self.toast(str(e));continue
                dev=devices.RemoteDevice(devices.slug(label),label,"openclaw-gateway",address,"~")
                devices.upsert(dev);self.cfg.reload_devices()
                token=self.prompt("GATEWAY TOKEN","",True)
                if token:self.secrets.set("gateway:managed-"+dev.id,token)
                self.status=f"ADDED OPENCLAW GATEWAY {label}"
            elif item=="ADD HERMES SERVER":
                label=self.prompt("HERMES SERVER NAME")
                if not label:continue
                address=self.prompt("HTTP OR HTTPS URL","http://100.")
                if not address:continue
                try:address=devices.validate_hermes_url(address)
                except ValueError as e:self.toast(str(e));continue
                dev=devices.RemoteDevice(devices.slug(label),label,"hermes-api",address,"~")
                devices.upsert(dev);self.cfg.reload_devices()
                token=self.prompt("REMOTE LOGIN TOKEN","",True)
                if token:self.secrets.set("gateway:managed-"+dev.id,token)
                self.status=f"ADDED HERMES SERVER {label}"
            else:
                credential=("CHANGE REMOTE LOGIN TOKEN" if item.kind=="hermes-api"
                            else "CHANGE GATEWAY TOKEN")
                action=self.pick(item.label,["TEST CONNECTION","PAIR SSH KEY",credential,"REMOVE DEVICE"])
                if action=="TEST CONNECTION":
                    if item.kind=="ssh":
                        self.draw_busy("TESTING SSH");ok,msg=remote.diagnose(item.address);self.toast(("READY: " if ok else "NOT READY: ")+msg)
                    else:self.test_gateway(item)
                elif action=="PAIR SSH KEY" and item.kind=="ssh":self.setup_ssh(item)
                elif action in ("CHANGE GATEWAY TOKEN","CHANGE REMOTE LOGIN TOKEN") and item.kind in ("openclaw-gateway","hermes-api"):
                    title=("REMOTE LOGIN TOKEN" if item.kind=="hermes-api" else "GATEWAY TOKEN")
                    token=self.prompt(title,self.secrets.get("gateway:managed-"+item.id) or "",True)
                    if token is not None:self.secrets.set("gateway:managed-"+item.id,token) if token else self.secrets.clear("gateway:managed-"+item.id)
                elif action=="REMOVE DEVICE":
                    devices.remove(item.id);self.secrets.clear("gateway:managed-"+item.id);self.cfg.reload_devices();self.status=f"REMOVED {item.label}"

    def setup_ssh(self,item):
        ok,value=remote.ensure_key()
        if not ok:self.toast("SSH KEY CREATION FAILED",[value]);return
        ready,msg=remote.diagnose(item.address)
        if ready:self.toast("SSH READY",[msg]);return
        method=self.pick("PAIR SSH KEY",[
            "PAIR WITH REMOTE PASSWORD",
            "SHOW PUBLIC KEY QR",
            "CANCEL",
        ],subtitle="PASSWORD IS USED ONCE; THE KEY IS USED AFTERWARDS")
        if method=="PAIR WITH REMOTE PASSWORD":
            password=self.prompt(f"PASSWORD / {item.label}","",True)
            if password is None:return
            self.draw_busy(f"PAIRING {item.label}")
            ok,msg=remote.pair_with_password(item.address,Path(value),password)
            if not ok:self.toast("SSH PAIRING FAILED",[msg]);return
        elif method=="SHOW PUBLIC KEY QR":
            try:public_key=Path(value).read_text("utf-8").strip()
            except OSError as exc:self.toast("PUBLIC KEY READ FAILED",[str(exc)]);return
            self.show_qr("SSH PUBLIC KEY",public_key,
                         "ADD TO AUTHORIZED_KEYS ON COMPUTER - B THEN TEST")
        else:return
        ok,msg=remote.diagnose(item.address)
        self.toast(("READY: " if ok else "PAIRING INCOMPLETE: ")+msg)

    def test_gateway(self,item):
        token=self.secrets.get("gateway:managed-"+item.id)
        env=os.environ.copy()
        if item.kind=="openclaw-gateway":
            argv=openclaw_gateway_health_argv()
            env["OPENCLAW_GATEWAY_URL"]=item.address
            if token:env["OPENCLAW_GATEWAY_TOKEN"]=token
        else:
            argv=[os.environ.get("PYTHON","python"),"-c",
                  "import os;from handai.hermes_remote import HermesRemote;print(HermesRemote(os.environ['HERMES_REMOTE_URL'],os.environ['HERMES_REMOTE_TOKEN']).request('/v1/capabilities'))"]
            env["HERMES_REMOTE_URL"]=item.address;env["HERMES_REMOTE_TOKEN"]=token or ""
        self.draw_busy("TESTING GATEWAY")
        try:r=subprocess.run(argv,capture_output=True,text=True,timeout=15,env=env)
        except (OSError,subprocess.TimeoutExpired) as e:self.toast(f"GATEWAY TEST FAILED: {e}");return
        self.toast("GATEWAY READY" if r.returncode==0 else "GATEWAY UNREACHABLE",[(r.stderr or r.stdout).strip()[:180]])

    def choose_skin(self):
        chosen=self.pick("PIXEL SKINS",THEMES,lambda t:f"{'*' if t.id==self.ui.theme.id else ' '} {t.label}",subtitle="10 COLOR THEMES - SAVED AUTOMATICALLY")
        if not chosen:return
        self.ui.apply_theme(chosen)
        try:save_theme(chosen);self.status=f"SKIN CHANGED: {chosen.label}"
        except OSError as e:self.status=f"SKIN SAVE FAILED: {e}"

    def draw_busy(self,msg):
        self.chrome("WORKING");self.ui.frame(80,165,480,100,self.ui.PINK,4);self.ui.text(110,207,msg,self.ui.YELLOW,2,max_chars=35);self.footer("PLEASE WAIT");self.ui.present()

    def demo_mode(self):
        ok,detail=demo.start()
        if not ok:self.toast("DEMO COULD NOT START",[detail]);return
        self.status=detail
        while True:
            self.chrome("OFFLINE DEMO","REAL TMUX SESSION - NO NETWORK, ACCOUNT OR API")
            self.ui.frame(18,88,604,322,self.ui.CYAN,3)
            output=demo.capture(13)
            for row,line in enumerate(output[-13:]):
                self.ui.text(32,105+row*22,line,self.ui.YELLOW if line.startswith(("YOU", "RESULT")) else self.ui.INK,1,max_chars=94)
            self.footer("A TYPE PROMPT   START AUTO DEMO   B BACK");self.ui.present()
            event=self.ui.event()
            if event=="a":
                prompt=self.prompt("OFFLINE DEMO PROMPT")
                if prompt:
                    sent,message=demo.send(prompt);self.status=message
                    if not sent:self.toast("DEMO SEND FAILED",[message])
                    else:time.sleep(0.15)
            elif event=="done":
                demo.send("Create a small code change and verify it")
                time.sleep(0.15)
            elif event in ("b","cancel","quit"):
                self.status="DEMO SESSION KEPT ACTIVE FOR PHONE KEYBOARD"
                return

    def run(self):
        self.unlock_credentials()
        # Kiosk/development deep-links intentionally bypass onboarding so a
        # single provider screen can be exercised in automated GUI tests.
        direct=os.environ.pop("HANDAI_PROVIDER_HOME","")
        direct_oauth=os.environ.pop("HANDAI_OAUTH_HOME","")
        if not direct and not direct_oauth:
            self.first_run()
        self.music.play("main")
        if direct:
            provider=next((p for p in self.cfg.providers if p.id==direct),None)
            if provider:self.provider_hub(provider)
        if direct_oauth:
            provider=next((p for p in self.cfg.providers if p.id==direct_oauth),None)
            if provider:self.oauth_login(provider)
        if os.environ.pop("HANDAI_VOICE_HOME","")=="1":self.voice_input()
        if os.environ.pop("HANDAI_AUDIO_HOME","")=="1":self.audio_settings()
        if os.environ.pop("HANDAI_MUSIC_HOME","")=="1":self.music_settings()
        menu=[("DEMO MODE",self.demo_mode),("NEW SESSION",self.new_session),("ACTIVE SESSIONS",self.sessions),("PROVIDERS / LOGIN",self.providers),("SKILLS HUB",self.skill_screen),("NETWORK",self.network),("VOICE INPUT",self.voice_input),("PHONE KEYBOARD",self.phone_keyboard),("INSTALL LOCAL AGENTS",self.install_agents),("SETTINGS",self.settings),("QUIT",None)]
        idx=0
        while True:
            self.chrome("HOME",self.status)
            for i,(label,_) in enumerate(menu):
                col=i%2;row=i//2;x=20+col*305;y=82+row*58;sel=i==idx
                self.ui.rect(x,y,295,46,self.ui.PANEL2 if sel else self.ui.PANEL);self.ui.frame(x,y,295,46,self.ui.CYAN if sel else self.ui.PANEL2,3)
                self.ui.text(x+18,y+16,label,self.ui.YELLOW if sel else self.ui.INK,2,max_chars=21)
            self.footer("D-PAD MOVE   A SELECT   B / Q QUIT");self.ui.present();e=self.ui.event()
            if e=="left":idx=(idx-1)%len(menu)
            elif e=="right":idx=(idx+1)%len(menu)
            elif e=="up":idx=(idx-2)%len(menu)
            elif e=="down":idx=(idx+2)%len(menu)
            elif e in ("a","done"):
                fn=menu[idx][1]
                if fn is None:return
                fn()
            elif e in ("b","cancel","quit"):return

    def install_agents(self):
        helper="/usr/sbin/handai-install-agents"
        if not os.path.exists(helper):self.toast("INSTALLER ONLY PRESENT ON DEVICE IMAGE");return
        self.interactive(["sh",helper]);self.status="RAN LOCAL AGENT INSTALLER"


def publish_gui_ready(
    ui: SDL, logger: Path = Path("/usr/sbin/handai-boot-log"),
    marker: Path | None = None,
) -> bool:
    """Persist proof that SDL and the handheld input backends initialized."""
    if os.name == "posix":
        marker=marker or Path(os.environ.get("HANDAI_GUI_READY","/run/handai-gui-ready"))
        try:
            marker.parent.mkdir(parents=True,exist_ok=True)
            marker.touch()
        except OSError:
            pass
    if os.name == "posix" and logger.exists():
        evdev = str(ui.evdev.path) if ui.evdev and ui.evdev.path else "none"
        controller = "yes" if ui.pad else "no"
        try:
            result = subprocess.run(
                [str(logger), "GUI_READY",
                 f"SDL ready; controller={controller}; evdev={evdev}"],
                timeout=4,
            )
            return result.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False
    return False


def main(config:Config,secrets:SecretStore):
    ui=SDL()
    cockpit=PixelCockpit(config,secrets,ui)
    publish_gui_ready(ui)
    try:cockpit.run()
    finally:cockpit.music.close();ui.close()
