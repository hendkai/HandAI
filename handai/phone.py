"""Token-paired phone keyboard served by the handheld itself."""

from __future__ import annotations

import base64
import html
import ipaddress
import secrets
import shlex
import socket
import subprocess
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import parse_qs,urlparse

from .tmux import SessionInfo

MAX_TEXT=4096


def local_ip() -> str:
    """Best-effort LAN address without sending traffic."""
    sock=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
    try:sock.connect(("192.0.2.1",9));return sock.getsockname()[0]
    except OSError:return "127.0.0.1"
    finally:sock.close()


def safe_ip(candidates) -> str|None:
    for raw in candidates:
        try:
            ip=ipaddress.ip_address(raw)
            if ip.version==4 and not ip.is_loopback:return str(ip)
        except ValueError:pass
    return None


def send_session_text(session:SessionInfo,text:str,enter:bool=True,timeout:float=8.0)->tuple[bool,str]:
    if not text or len(text)>MAX_TEXT:return False,"text must contain 1-4096 characters"
    try:
        if not session.host:
            from .compose import send_text
            return send_text(session.name,text,enter,timeout)
        # The target came from tmux inventory, but quote it anyway. The text is
        # base64 and decoded through stdin on the remote side, never evaluated.
        payload=base64.b64encode(text.encode()).decode("ascii")
        target=shlex.quote(session.name)
        command=(f"printf %s {payload} | base64 -d | tmux load-buffer -b handai-phone - && "
                 f"tmux paste-buffer -b handai-phone -t {target}"+
                 (f" && tmux send-keys -t {target} Enter" if enter else ""))
        from .remote import ssh_argv
        r=subprocess.run(ssh_argv(session.host,command,batch=True),capture_output=True,text=True,timeout=timeout)
        return r.returncode==0,(r.stderr.strip() or "sent")
    except (OSError,subprocess.TimeoutExpired) as e:return False,f"send failed: {e}"


_PAGE="""<!doctype html><html><head><meta name=viewport content='width=device-width,initial-scale=1'>
<title>HandAI Phone Keyboard</title><style>body{background:#0a0e19;color:#e0eee2;font:18px monospace;max-width:720px;margin:auto;padding:24px}h1{color:#32d7cf}textarea{box-sizing:border-box;width:100%;height:45vh;background:#121b2b;color:white;border:3px solid #32d7cf;padding:14px;font:20px monospace}button{width:100%;padding:18px;margin-top:12px;background:#fac740;border:0;font:bold 20px monospace}label{display:block;margin:14px 0}.ok{color:#5ed376}</style></head><body><h1>HANDAI PHONE KEYBOARD</h1><p>Target: {target}</p><form method=post action='/send'><input type=hidden name=csrf value='{csrf}'><textarea autofocus name=text maxlength=4096 placeholder='Type for the agent...'></textarea><label><input type=checkbox name=enter checked> Press Enter after sending</label><button>SEND TO GAMEBOY</button></form><p class=ok>{message}</p></body></html>"""


@dataclass
class PhoneKeyboard:
    session:SessionInfo
    host:str
    port:int=0
    lifetime:float=900.0

    def __post_init__(self):
        self.token=secrets.token_urlsafe(24);self.cookie_token=secrets.token_urlsafe(32)
        self.csrf=secrets.token_urlsafe(24);self.paired=False
        self.started=time.monotonic();self.last_message=""
        outer=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*_):pass
            def _cookie(self):
                parts={}
                for item in self.headers.get("Cookie","").split(";"):
                    if "=" in item:
                        key,value=item.strip().split("=",1);parts[key]=value
                return parts.get("handai_pair","")
            def _auth(self):return secrets.compare_digest(self._cookie(),outer.cookie_token)
            def _headers(self):
                self.send_header("Cache-Control","no-store")
                self.send_header("Referrer-Policy","no-referrer")
                self.send_header("X-Content-Type-Options","nosniff")
                self.send_header("Content-Security-Policy","default-src 'none'; style-src 'unsafe-inline'; form-action 'self'")
            def _page(self,status=200):
                if not self._auth():self.send_error(403);return
                body=(_PAGE.replace("{target}",html.escape(outer.session.name))
                      .replace("{csrf}",outer.csrf)
                      .replace("{message}",html.escape(outer.last_message))).encode()
                self.send_response(status);self.send_header("Content-Type","text/html; charset=utf-8");self._headers();self.send_header("Content-Length",str(len(body)));self.end_headers();self.wfile.write(body)
            def do_GET(self):
                supplied=parse_qs(urlparse(self.path).query).get("pair",[""])[0]
                if not outer.paired and secrets.compare_digest(supplied,outer.token):
                    outer.paired=True;self.send_response(303)
                    self.send_header("Set-Cookie",f"handai_pair={outer.cookie_token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={int(outer.lifetime)}")
                    self._headers();self.send_header("Location","/");self.end_headers();return
                self._page()
            def do_POST(self):
                if not self._auth():self.send_error(403);return
                try:length=int(self.headers.get("Content-Length","0"))
                except ValueError:length=0
                if length<1 or length>MAX_TEXT*4:self.send_error(413);return
                form=parse_qs(self.rfile.read(length).decode("utf-8","replace"),keep_blank_values=True)
                if not secrets.compare_digest(form.get("csrf",[""])[0],outer.csrf):self.send_error(403);return
                ok,msg=send_session_text(outer.session,form.get("text",[""])[0],"enter" in form)
                outer.last_message="SENT" if ok else msg;self._page(200 if ok else 500)
        self.server=ThreadingHTTPServer(("0.0.0.0",self.port),Handler)
        self.server.timeout=.5;self.port=self.server.server_address[1]
        self.thread=threading.Thread(target=self._run,name="handai-phone",daemon=True)

    @property
    def url(self):return f"http://{self.host}:{self.port}/?pair={self.token}"
    def start(self):self.thread.start();return self
    def _run(self):
        while time.monotonic()-self.started<self.lifetime:self.server.handle_request()
        self.server.server_close()
    def stop(self):
        self.started-=self.lifetime
        try:
            with socket.create_connection(("127.0.0.1",self.port),timeout=.2):pass
        except OSError:pass
        self.thread.join(timeout=1);self.server.server_close()


def parse_pbm(data:bytes)->list[list[bool]]:
    """Read P1/P4 output from qrencode into a renderer-neutral matrix."""
    pos=0;tokens=[]
    while len(tokens)<3:
        while pos<len(data) and chr(data[pos]).isspace():pos+=1
        if pos<len(data) and data[pos]==35:
            pos=data.find(b"\n",pos)+1;continue
        end=pos
        while end<len(data) and not chr(data[end]).isspace():end+=1
        tokens.append(data[pos:end].decode("ascii"));pos=end
    magic,w,h=tokens[0],int(tokens[1]),int(tokens[2])
    if magic=="P1":
        while pos<len(data) and chr(data[pos]).isspace():pos+=1
        bits=[c==49 for c in data[pos:] if c in (48,49)]
    elif magic=="P4":
        if pos<len(data) and chr(data[pos]).isspace():pos+=1
        stride=(w+7)//8;bits=[]
        for y in range(h):
            row=data[pos+y*stride:pos+(y+1)*stride]
            bits.extend(bool(row[x//8]&(128>>(x%8))) for x in range(w))
    else:raise ValueError("unsupported PBM")
    if len(bits)<w*h:raise ValueError("truncated PBM")
    return [bits[y*w:(y+1)*w] for y in range(h)]


def qr_matrix(value:str)->list[list[bool]]:
    try:
        r=subprocess.run(["qrencode","-t","ASCII","-o","-",value],capture_output=True,text=True,timeout=5)
        if r.returncode==0:
            lines=r.stdout.splitlines();width=max((len(line) for line in lines),default=0)
            matrix=[["#" in line[x:x+2] for x in range(0,width,2)] for line in (line.ljust(width) for line in lines)]
            if matrix and len(matrix)==len(matrix[0]):return matrix
    except (OSError,subprocess.TimeoutExpired):pass
    try:
        import qrcode # Windows development fallback only
        qr=qrcode.QRCode(border=4);qr.add_data(value);qr.make(fit=True);return qr.get_matrix()
    except ImportError as e:raise RuntimeError("qrencode is not installed") from e
