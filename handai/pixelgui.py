"""SDL2 pixel-art cockpit for the 640x480 RG35xxSP display.

The module deliberately uses ctypes instead of pygame/PySDL2: SDL2 is already
part of the image and HandAI's Python package remains dependency-free.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import importlib.util
import os
import subprocess
from pathlib import Path
from typing import Callable, Sequence, TypeVar

from . import network, remote, skills, tmux
from .config import Config, config_path
from .providers import Mode, Provider
from .router import build_target
from .secrets import SecretStore

T = TypeVar("T")

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
        self._bind(); self.window=None; self.renderer=None; self.pad=None; self.open()

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
                return {0:"a",1:"b",2:"cancel",6:"b",7:"done",11:"up",12:"down",13:"left",14:"right"}.get(button,"none")
        return "quit"


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
        if p.auth=="token-env" and p.token_env:
            token=self.secrets.get(p.id)
            if token: os.environ[p.token_env]=token
        for k,v in p.env.items(): os.environ.setdefault(k,v)
        os.environ["HANDAI_SKILLS"]=str(self.hub)

    def login(self,p:Provider):
        if p.auth=="none": self.toast(f"{p.label} NEEDS NO LOGIN")
        elif p.auth=="oauth-device":
            if p.login_command: self.interactive(p.login_command); self.status=f"RAN {p.label} LOGIN"
            else: self.toast("NO LOGIN COMMAND CONFIGURED")
        else:
            token=self.prompt(f"{p.label} TOKEN",self.secrets.get(p.id) or "",True)
            if token is None: return
            if token: self.secrets.set(p.id,token); self.status=f"TOKEN STORED FOR {p.label}"
            else: self.secrets.clear(p.id); self.status=f"TOKEN CLEARED FOR {p.label}"

    def new_session(self):
        p=self.pick("NEW / PROVIDER",self.cfg.providers,lambda x:f"{x.label}  [{x.auth}]")
        if not p:return
        if p.auth=="token-env" and not self.secrets.has(p.id):
            self.toast("TOKEN REQUIRED - OPENING LOGIN"); self.login(p)
            if not self.secrets.has(p.id):return
        m=self.pick("NEW / MODE",self.cfg.modes_for(p),lambda x:f"{x.label}  {x.host or 'DEVICE'}")
        if not m:return
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
        p=self.pick("PROVIDERS",self.cfg.providers,lambda x:f"{'+' if x.auth!='token-env' or self.secrets.has(x.id) else 'X'} {x.label} [{x.auth}]")
        if not p:return
        if p.auth=="none": self.toast("NO AUTHENTICATION NEEDED");return
        acts=["LOGIN"] if p.auth=="oauth-device" else ["ENTER TOKEN"]
        if p.auth=="token-env" and self.secrets.has(p.id):
            if any(m.is_remote for m in self.cfg.modes_for(p)):acts.append("PUSH TOKEN TO HOST")
            acts.append("CLEAR TOKEN")
        act=self.pick(p.label,acts)
        if act in ("LOGIN","ENTER TOKEN"):self.login(p)
        elif act=="CLEAR TOKEN":self.secrets.clear(p.id);self.status=f"TOKEN CLEARED FOR {p.label}"
        elif act=="PUSH TOKEN TO HOST":
            hosts=sorted({m.host for m in self.cfg.modes_for(p) if m.is_remote and m.host});host=self.pick("PUSH TOKEN",hosts)
            if host:
                self.draw_busy(f"PUSHING TO {host}");_,msg=remote.push_token(host,p.token_env or "",self.secrets.get(p.id) or "");self.status=msg;self.toast(msg)

    def network(self):
        if not network.available():self.toast("WIFI CONTROL UNAVAILABLE - NO WPA_CLI");return
        act=self.pick("NETWORK",["SCAN AND CONNECT","SAVED NETWORKS","STATUS"],subtitle=network.status())
        if act=="STATUS":self.toast(network.status())
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

    def sync_local(self,quiet=False):
        results=[]
        for p in self.cfg.providers:
            if p.skills_dir: ok,_=skills.link_into(self.hub,p.skills_dir);results.append(f"{'+' if ok else 'X'} {p.label}")
        self.status="SKILLS SYNCED: "+", ".join(results)
        if not quiet:self.toast(self.status)

    def skill_screen(self):
        installed=skills.list_installed(self.hub)
        act=self.pick("SKILLS",["INSTALL FROM INTERNET",f"INSTALLED SKILLS ({len(installed)})","SYNC TO TOOLS LOCAL","SYNC TO REMOTE HOSTS"])
        if act=="INSTALL FROM INTERNET":
            spec=self.prompt("SKILL SOURCE")
            if not spec:return
            try:src=skills.parse_source(spec)
            except ValueError as e:self.toast(str(e));return
            if self.pick(f"INSTALL {src.name}",["YES DOWNLOAD","CANCEL"])!="YES DOWNLOAD":return
            self.draw_busy(f"INSTALLING {src.name}")
            try:sk=skills.install(self.hub,spec);self.sync_local(True);self.toast(f"INSTALLED {sk.name} AND SYNCED")
            except (ValueError,OSError) as e:self.toast(f"INSTALL FAILED: {e}")
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

    def settings(self):
        self.toast("SYSTEM STATUS",[f"CONFIG: {config_path()}",f"STATE: {self.secrets.path}",network.status(),f"PROVIDERS: {len(self.cfg.providers)}  MODES: {len(self.cfg.modes)}","GUI: SDL2 PIXEL / 640X480"])

    def draw_busy(self,msg):
        self.chrome("WORKING");self.ui.frame(80,165,480,100,self.ui.PINK,4);self.ui.text(110,207,msg,self.ui.YELLOW,2,max_chars=35);self.footer("PLEASE WAIT");self.ui.present()

    def run(self):
        menu=[("NEW SESSION",self.new_session),("ACTIVE SESSIONS",self.sessions),("PROVIDERS / LOGIN",self.providers),("SKILLS HUB",self.skill_screen),("NETWORK",self.network),("INSTALL LOCAL AGENTS",self.install_agents),("SETTINGS",self.settings),("QUIT",None)]
        idx=0
        while True:
            self.chrome("HOME",self.status)
            for i,(label,_) in enumerate(menu):
                col=i%2;row=i//2;x=20+col*305;y=100+row*78;sel=i==idx
                self.ui.rect(x,y,295,62,self.ui.PANEL2 if sel else self.ui.PANEL);self.ui.frame(x,y,295,62,self.ui.CYAN if sel else self.ui.PANEL2,3)
                self.ui.text(x+18,y+24,label,self.ui.YELLOW if sel else self.ui.INK,2,max_chars=21)
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
