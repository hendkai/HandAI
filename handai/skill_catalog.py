"""Gamepad-friendly reader for the public skills.sh leaderboard.

The documented v1 API requires Vercel OIDC, which a handheld cannot have. The
public leaderboard HTML contains the same visible rank/name/source/install data
and needs no account. Results are cached so a brief outage does not break browse.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict,dataclass
from html import unescape
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request,urlopen

BASE="https://skills.sh/"
VIEWS=("all-time","trending","hot")
_VIEW_PATH={"all-time":"/","trending":"/trending","hot":"/hot"}
_ROW=re.compile(
    r'href="/([^"/]+/[^"/]+/[^"/]+)"[^>]*>.*?'
    r'<h3[^>]*>([^<]+)</h3>.*?<p[^>]*>([^<]+)</p>.*?'
    r'<span class="font-mono text-sm text-foreground">([^<]+)</span>.*?</a>',re.S)


@dataclass(frozen=True)
class CatalogSkill:
    id:str
    name:str
    source:str
    installs:str
    rank:int

    @property
    def slug(self):return self.id.rsplit("/",1)[-1]
    @property
    def install_url(self):return f"https://github.com/{self.source}.git"


def parse_leaderboard(text:str,limit:int=50)->list[CatalogSkill]:
    out=[];seen=set()
    for match in _ROW.finditer(text):
        sid,name,source,count=(unescape(v).strip() for v in match.groups())
        if sid in seen or not sid.startswith(source+"/"):continue
        seen.add(sid);out.append(CatalogSkill(sid,name,source,count,len(out)+1))
        if len(out)>=limit:break
    return out


def _cache_path(view:str,page:int)->Path:
    state=os.environ.get("HANDAI_STATE") or os.path.expanduser("~/.local/state/handai")
    return Path(os.path.expandvars(os.path.expanduser(state)))/"catalog"/f"{view}-{page}.json"


def fetch(view:str="all-time",page:int=0,limit:int=30,timeout:float=15.0,cache_age:float=600)->list[CatalogSkill]:
    if view not in VIEWS:raise ValueError(f"unknown catalog view: {view}")
    cache=_cache_path(view,page)
    if cache.exists() and time.time()-cache.stat().st_mtime<cache_age:
        try:return [CatalogSkill(**item) for item in json.loads(cache.read_text("utf-8"))]
        except (OSError,ValueError,TypeError):pass
    url=BASE.rstrip("/")+_VIEW_PATH[view]+"?"+urlencode({"page":page})
    try:
        req=Request(url,headers={"User-Agent":"HandAI/0.1 skill browser"})
        with urlopen(req,timeout=timeout) as response:
            rows=parse_leaderboard(response.read(2_000_000).decode("utf-8","replace"),limit)
        if not rows:raise ValueError("skills.sh returned no leaderboard entries")
        cache.parent.mkdir(parents=True,exist_ok=True)
        cache.write_text(json.dumps([asdict(row) for row in rows],indent=2)+"\n","utf-8")
        return rows
    except (OSError,ValueError):
        try:return [CatalogSkill(**item) for item in json.loads(cache.read_text("utf-8"))]
        except (OSError,ValueError,TypeError):raise
