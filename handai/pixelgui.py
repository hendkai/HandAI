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
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence, TypeVar

from . import devices, diagnostics, hardware_report, network, phone, power, preferences, remote, skill_catalog, skills, tailscale, tmux
from .config import Config, config_path
from .providers import Mode, Provider
from .router import build_target
from .secrets import SecretStore

T = TypeVar("T")


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


THEMES = (
    Theme("neon-night","NEON NIGHT",(10,14,25),(18,27,43),(28,39,57),(224,238,226),(112,132,145),(50,215,207),(250,199,64),(238,91,137),(94,211,118)),
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
 ".":("00000","00000","00000","00000","00000","00110","00110"),":":("00000","00110","00110","00000","00110","00110","00000"),
 "/":("00001","00010","00010","00100","01000","01000","10000"),"\\":("10000","01000","01000","00100","00010","00010","00001"),
 "?":("01110","10001","00001","00010","00100","00000","00100"),"!":("00100","00100","00100","00100","00100","00000","00100"),
 "+":("00000","00100","00100","11111","00100","00100","00000"),"*":("00000","10101","01110","11111","01110","10101","00000"),
 "[":("01110","01000","01000","01000","01000","01000","01110"),"]":("01110","00010","00010","00010","00010","00010","01110"),
 "(":("00010","00100","01000","01000","01000","00100","00010"),")":("01000","00100","00010","00010","00010","00100","01000"),
 "=":("00000","11111","00000","11111","00000","00000","00000"),"@":("01110","10001","10111","10101","10111","10000","01110"),
 "#":("01010","11111","01010","01010","11111","01010","00000"),"'":("00100","00100","00000","00000","00000","00000","00000"),
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
        self._bind(); self.window=None; self.renderer=None; self.pad=None
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
        s.SDL_NumJoysticks.restype=ctypes.c_int; s.SDL_IsGameController.argtypes=[ctypes.c_int]
        s.SDL_GameControllerOpen.argtypes=[ctypes.c_int]; s.SDL_GameControllerOpen.restype=ctypes.c_void_p
        s.SDL_GameControllerClose.argtypes=[ctypes.c_void_p]
        s.SDL_DestroyRenderer.argtypes=[ctypes.c_void_p]; s.SDL_DestroyWindow.argtypes=[ctypes.c_void_p]
        s.SDL_Quit.argtypes=[]
        s.SDL_GetError.restype=ctypes.c_char_p

    def open(self):
        if self.s.SDL_Init(0x20|0x2000)!=0: raise RuntimeError(self.s.SDL_GetError().decode())
        fullscreen=0x1001 if os.environ.get("HANDAI_FULLSCREEN", "1" if os.path.exists("/dev/dri") else "0")!="0" else 0x4
        self.window=self.s.SDL_CreateWindow(b"HandAI Pixel Cockpit",0x2FFF0000,0x2FFF0000,self.W,self.H,fullscreen)
        if not self.window: raise RuntimeError(self.s.SDL_GetError().decode())
        self.renderer=self.s.SDL_CreateRenderer(self.window,-1,0x2|0x4) or self.s.SDL_CreateRenderer(self.window,-1,0)
        if not self.renderer: raise RuntimeError(self.s.SDL_GetError().decode())
        self.s.SDL_RenderSetLogicalSize(self.renderer,self.W,self.H)
        for i in range(self.s.SDL_NumJoysticks()):
            if self.s.SDL_IsGameController(i): self.pad=self.s.SDL_GameControllerOpen(i); break

    def close(self):
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
    def event(self):
        buf=(ctypes.c_uint8*64)()
        while self.s.SDL_WaitEvent(ctypes.byref(buf)):
            typ=int.from_bytes(bytes(buf[0:4]),"little")
            if typ==0x100: return "quit"
            if typ==0x300:
                key=int.from_bytes(bytes(buf[20:24]),"little",signed=True)
                return {1073741906:"up",1073741905:"down",1073741904:"left",1073741903:"right",13:"done",32:"a",27:"cancel",8:"b",113:"quit"}.get(key,"none")
            if typ==0x651:
                button=buf[12]
                return self.button_map.get(button,"none")
        return "quit"

    def raw_button(self)->int|None:
        """Wait for one controller button; Escape cancels calibration."""
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

    def chrome(self,title:str,subtitle:str=""):
        u=self.ui; u.clear(); u.rect(0,0,640,58,u.PANEL); u.rect(0,55,640,3,u.CYAN)
        # Tiny pixel bot logo.
        u.rect(20,14,30,30,u.CYAN); u.rect(25,19,20,16,u.BG); u.rect(29,23,4,4,u.YELLOW); u.rect(38,23,4,4,u.YELLOW)
        u.text(64,12,"HANDAI",u.INK,3); u.text(64,38,"PIXEL COCKPIT",u.CYAN,1)
        short=title[:24]; u.text(max(322,620-len(short)*12),20,short,u.YELLOW,2)
        if subtitle: u.text(22,72,subtitle,u.MUTED,1,max_chars=96)

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
        chars=list("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789 /._-:@#+~")
        cols=12; value=initial; pos=0
        while True:
            self.chrome(title,"ON-SCREEN KEYBOARD")
            shown="*"*len(value) if secret else value
            self.ui.rect(20,86,600,42,self.ui.PANEL2); self.ui.text(34,100,shown[-47:],self.ui.YELLOW,2,max_chars=47)
            for i,ch in enumerate(chars):
                x=20+(i%cols)*50; y=145+(i//cols)*38
                self.ui.rect(x,y,44,31,self.ui.CYAN if i==pos else self.ui.PANEL)
                self.ui.text(x+16,y+9,ch,self.ui.BG if i==pos else self.ui.INK,2)
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
        self.ui.close()
        try: subprocess.call(argv)
        except OSError as e: self.status=f"LAUNCH FAILED: {e}"
        finally: self.ui.open()

    def env(self,p:Provider):
        if p.supports_auth("token-env") and p.token_env:
            token=self.secrets.get(p.id)
            if token: os.environ[p.token_env]=token
        for k,v in p.env.items(): os.environ.setdefault(k,v)
        os.environ["HANDAI_SKILLS"]=str(self.hub)

    def oauth_login(self,p:Provider,host:str|None=None):
        if not p.login_command:self.toast("NO OAUTH LOGIN COMMAND CONFIGURED");return
        argv=p.login_command if not host else remote.ssh_argv(host,shlex.join(p.login_command),tty=True)
        self.interactive(argv);self.status=f"RAN {p.label} OAUTH"+(f" ON {host}" if host else "")

    def api_login(self,p:Provider):
        if not p.token_env:self.toast("NO ACCESS TOKEN VARIABLE CONFIGURED");return
        token=self.prompt(f"{p.label} ACCESS TOKEN",self.secrets.get(p.id) or "",True)
        if token is None:return
        if token:self.secrets.set(p.id,token);self.status=f"ACCESS TOKEN STORED FOR {p.label}"
        else:self.secrets.clear(p.id);self.status=f"ACCESS TOKEN CLEARED FOR {p.label}"

    def new_session(self):
        p=self.pick("NEW / PROVIDER",self.cfg.providers,lambda x:f"{x.label}  [{x.auth}]")
        if not p:return
        if p.supports_auth("token-env") and not p.supports_auth("oauth-device") and not self.secrets.has(p.id):
            self.toast("ACCESS CREDENTIAL REQUIRED - OPENING ADVANCED LOGIN"); self.api_login(p)
            if not self.secrets.has(p.id):return
        m=self.pick("NEW / MODE",self.cfg.modes_for(p),lambda x:f"{x.label}  {x.host or 'DEVICE'}")
        if not m:return
        if m.transport=="openclaw-gateway":
            os.environ["OPENCLAW_GATEWAY_URL"]=m.endpoint or ""
            gateway_token=self.secrets.get("gateway:"+m.id)
            if gateway_token:os.environ["OPENCLAW_GATEWAY_TOKEN"]=gateway_token
        elif m.transport=="hermes-api":
            os.environ["HERMES_REMOTE_URL"]=m.endpoint or ""
            remote_key=self.secrets.get("gateway:"+m.id)
            if remote_key:os.environ["HERMES_REMOTE_API_KEY"]=remote_key
        choices=list(self.cfg.recent_workdirs)
        if m.default_workdir and m.default_workdir not in choices: choices.insert(0,m.default_workdir)
        choices.append("<ENTER PATH>"); wd=self.pick("NEW / WORKDIR",choices)
        if wd=="<ENTER PATH>": wd=self.prompt("PATH ON TARGET",m.default_workdir or "~/")
        if wd is None:return
        try: self.env(p); target=build_target(p,m,wd)
        except ValueError as e:self.toast(str(e));return
        self.status=f"LAUNCHING {target.display}"; self.interactive(target.argv); self.status=f"DETACHED FROM {p.label}"

    def sessions(self):
        self.draw_busy("SCANNING SESSIONS")
        found=tmux.list_all(self.cfg.modes)
        s=self.pick("SESSIONS",found,lambda x:f"{'*' if x.attached else 'O'} {x.name} [{x.host or 'DEVICE'}]")
        if not s:return
        act=self.pick(s.name,["ATTACH","KILL SESSION"])
        if act=="ATTACH":self.interactive(tmux.attach_argv(s));self.status=f"DETACHED FROM {s.name}"
        elif act=="KILL SESSION":self.status=f"KILLED {s.name}" if tmux.kill(s) else f"KILL FAILED: {s.name}"

    def providers(self):
        area=self.pick("PROVIDERS / LOGIN",["LOCAL PROVIDERS","REMOTE PROVIDERS"])
        if area=="LOCAL PROVIDERS":
            local=next((m for m in self.cfg.modes if not m.is_remote),None)
            candidates=[p for p in self.cfg.providers if local and p.allows_mode(local.id)]
            p=self.pick("LOCAL PROVIDERS",candidates,self.provider_label,subtitle="RUNS ON THIS HANDHELD")
            if p:self.provider_login(p,None)
        elif area=="REMOTE PROVIDERS":
            remote_modes=[m for m in self.cfg.modes if m.is_remote]
            candidates=[p for p in self.cfg.providers
                        if any(m in self.cfg.modes_for(p) for m in remote_modes)]
            p=self.pick("REMOTE PROVIDERS",candidates,
                        lambda p:f"REMOTE {self.provider_label(p)}",
                        subtitle="CHOOSE THE AGENT ON THE OTHER DEVICE")
            if not p:return
            modes=[m for m in remote_modes if m in self.cfg.modes_for(p)]
            mode=self.pick(f"REMOTE {p.label}",modes,
                           lambda m:f"{m.label} [{m.host}]",
                           subtitle="CHOOSE THE DEVICE RUNNING THE AGENT")
            if mode:self.provider_login(p,mode.host)

    def provider_label(self,p):
        ready=p.supports_auth("oauth-device") or self.secrets.has(p.id)
        return f"{'+' if ready else 'X'} {p.label} [{' + '.join(p.auth_methods or [p.auth])}]"

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
            self.draw_busy("SCANNING WIFI"); nets=network.scan(); n=self.pick("WIFI NETWORKS",nets,lambda x:f"{'*' if x.secured else ' '} {x.ssid} {x.signal} DBM")
            if not n:return
            psk=self.prompt(f"PASSWORD / {n.ssid}",secret=True) if n.secured else None
            if n.secured and psk is None:return
            self.draw_busy(f"CONNECTING {n.ssid}");ok=network.connect(n.ssid,psk);self.status=network.status();self.toast("CONNECTED" if ok else "CONNECTION FAILED")
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
        ts=tailscale.status();host=phone.safe_ip(ts.ips) if ts.online else None
        host=host or phone.local_ip()
        if host=="127.0.0.1":self.toast("NO LAN OR TAILSCALE IP FOUND");return
        bridge=phone.PhoneKeyboard(session,host).start()
        try:
            self.show_qr("PAIR PHONE KEYBOARD",bridge.url,"SCAN QR - TYPE ON PHONE - B STOPS SHARING")
        finally:bridge.stop()
        self.status="PHONE KEYBOARD STOPPED"

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
        act=self.pick("SETTINGS",["REMOTE DEVICES","SYSTEM DIAGNOSTICS","HARDWARE ACCEPTANCE REPORT","SYSTEM POWER","SECURE CREDENTIALS WITH PIN","GAMEPAD CALIBRATION","RUN SETUP WIZARD","CHOOSE PIXEL SKIN","SYSTEM STATUS"],subtitle=f"ACTIVE SKIN: {self.ui.theme.label}")
        if act=="REMOTE DEVICES": self.remote_devices()
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
            self.toast("SYSTEM STATUS",[f"CONFIG: {config_path()}",f"STATE: {self.secrets.path}",network.status(),f"PROVIDERS: {len(self.cfg.providers)}  MODES: {len(self.cfg.modes)}",f"SKIN: {self.ui.theme.label}","GUI: SDL2 PIXEL / 640X480"])

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
            if not self.ui.pad:self.toast("NO SDL GAME CONTROLLER DETECTED");return
            mapping={}
            for action,label in (("a","SELECT / A"),("b","BACK / B"),("done","START / DONE"),("cancel","MENU / CANCEL"),("up","D-PAD UP"),("down","D-PAD DOWN"),("left","D-PAD LEFT"),("right","D-PAD RIGHT")):
                self.chrome("GAMEPAD CALIBRATION",f"PRESS {label} - ESC CANCELS")
                self.ui.frame(60,150,520,130,self.ui.CYAN,4);self.ui.text(105,205,f"PRESS {label}",self.ui.YELLOW,2,max_chars=35)
                self.footer("PRESS THE REQUESTED PHYSICAL BUTTON");self.ui.present()
                button=self.ui.raw_button()
                if button is None:self.toast("CALIBRATION CANCELLED");return
                mapping[button]=action
            preferences.save_button_map(mapping);self.ui.button_map=mapping;self.status="GAMEPAD CALIBRATION SAVED"
        elif choice=="USE STANDARD SDL MAPPING":
            preferences.save_button_map(preferences.DEFAULT_BUTTONS);self.ui.button_map=preferences.button_map();self.status="STANDARD GAMEPAD MAP RESTORED"
        elif choice=="INFO":self.toast("RG35XXSP BUTTONS USE SDL STANDARD LAYOUT",["A SELECTS, B GOES BACK, START CONFIRMS.","SET SDL_GAMECONTROLLERCONFIG FOR CUSTOM FIRMWARE MAPS."])

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
                token=self.prompt("HERMES API ACCESS TOKEN","",True)
                if token:self.secrets.set("gateway:managed-"+dev.id,token)
                self.status=f"ADDED HERMES SERVER {label}"
            else:
                action=self.pick(item.label,["TEST CONNECTION","PAIR SSH KEY","CHANGE GATEWAY TOKEN","REMOVE DEVICE"])
                if action=="TEST CONNECTION":
                    if item.kind=="ssh":
                        self.draw_busy("TESTING SSH");ok,msg=remote.diagnose(item.address);self.toast(("READY: " if ok else "NOT READY: ")+msg)
                    else:self.test_gateway(item)
                elif action=="PAIR SSH KEY" and item.kind=="ssh":self.setup_ssh(item)
                elif action=="CHANGE GATEWAY TOKEN" and item.kind in ("openclaw-gateway","hermes-api"):
                    token=self.prompt("GATEWAY TOKEN",self.secrets.get("gateway:managed-"+item.id) or "",True)
                    if token is not None:self.secrets.set("gateway:managed-"+item.id,token) if token else self.secrets.clear("gateway:managed-"+item.id)
                elif action=="REMOVE DEVICE":
                    devices.remove(item.id);self.secrets.clear("gateway:managed-"+item.id);self.cfg.reload_devices();self.status=f"REMOVED {item.label}"

    def setup_ssh(self,item):
        ok,value=remote.ensure_key()
        if not ok:self.toast("SSH KEY CREATION FAILED",[value]);return
        self.interactive(remote.pair_command(item.address,value))
        ok,msg=remote.diagnose(item.address);self.toast(("READY: " if ok else "PAIRING INCOMPLETE: ")+msg)

    def test_gateway(self,item):
        token=self.secrets.get("gateway:managed-"+item.id)
        env=os.environ.copy()
        if item.kind=="openclaw-gateway":
            argv=["openclaw","gateway","health","--json"]
            env["OPENCLAW_GATEWAY_URL"]=item.address
            if token:env["OPENCLAW_GATEWAY_TOKEN"]=token
        else:
            argv=[os.environ.get("PYTHON","python"),"-c",
                  "import os;from handai.hermes_remote import HermesRemote;print(HermesRemote(os.environ['HERMES_REMOTE_URL'],os.environ['HERMES_REMOTE_API_KEY']).request('/v1/capabilities'))"]
            env["HERMES_REMOTE_URL"]=item.address;env["HERMES_REMOTE_API_KEY"]=token or ""
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

    def run(self):
        self.unlock_credentials()
        self.first_run()
        menu=[("NEW SESSION",self.new_session),("ACTIVE SESSIONS",self.sessions),("PROVIDERS / LOGIN",self.providers),("SKILLS HUB",self.skill_screen),("NETWORK",self.network),("PHONE KEYBOARD",self.phone_keyboard),("INSTALL LOCAL AGENTS",self.install_agents),("SETTINGS",self.settings),("QUIT",None)]
        idx=0
        while True:
            self.chrome("HOME",self.status)
            for i,(label,_) in enumerate(menu):
                col=i%2;row=i//2;x=20+col*305;y=94+row*67;sel=i==idx
                self.ui.rect(x,y,295,54,self.ui.PANEL2 if sel else self.ui.PANEL);self.ui.frame(x,y,295,54,self.ui.CYAN if sel else self.ui.PANEL2,3)
                self.ui.text(x+18,y+20,label,self.ui.YELLOW if sel else self.ui.INK,2,max_chars=21)
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


def main(config:Config,secrets:SecretStore):
    ui=SDL()
    try:PixelCockpit(config,secrets,ui).run()
    finally:ui.close()
