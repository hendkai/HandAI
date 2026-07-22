"""Minimal terminal client for the official Hermes Sessions API."""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request


class HermesRemote:
    def __init__(self,base_url:str,key:str,timeout:float=300):
        self.base=base_url.rstrip("/");self.key=key;self.timeout=timeout

    def request(self,path:str,payload:dict|None=None):
        data=json.dumps(payload).encode() if payload is not None else None
        req=urllib.request.Request(self.base+path,data=data,
            headers={"Authorization":"Bearer "+self.key,"Content-Type":"application/json","Accept":"application/json"},
            method="POST" if data is not None else "GET")
        with urllib.request.urlopen(req,timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def create_session(self)->str:
        result=self.request("/api/sessions",{})
        session=result.get("id") or result.get("session_id") or result.get("session",{}).get("id")
        if not session:raise RuntimeError("Hermes did not return a session id")
        return str(session)

    def chat(self,session:str,text:str)->str:
        result=self.request(f"/api/sessions/{session}/chat",{"input":text})
        value=(result.get("response") or result.get("content") or result.get("output")
               or result.get("message") or result)
        if isinstance(value,dict):value=value.get("content") or value.get("text") or value
        return value if isinstance(value,str) else json.dumps(value,indent=2)


def main(argv=None)->int:
    parser=argparse.ArgumentParser(prog="handai-hermes-remote")
    parser.add_argument("--url",default=os.environ.get("HERMES_REMOTE_URL"))
    args=parser.parse_args(argv)
    key=os.environ.get("HERMES_REMOTE_API_KEY","")
    if not args.url or not key:
        print("Hermes remote URL/API credential is missing.");return 2
    client=HermesRemote(args.url,key)
    try:session=client.create_session()
    except Exception as e:print(f"Connection failed: {e}");return 1
    print(f"HandAI connected to Hermes remote session {session}. /quit exits.")
    while True:
        try:text=input("you> ").strip()
        except (EOFError,KeyboardInterrupt):print();break
        if text in ("/quit","/exit"):break
        if not text:continue
        try:print("hermes> "+client.chat(session,text))
        except (OSError,urllib.error.HTTPError,ValueError,RuntimeError) as e:print(f"error> {e}")
    return 0


if __name__=="__main__":raise SystemExit(main())
