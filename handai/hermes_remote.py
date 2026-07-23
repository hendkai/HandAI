"""Minimal terminal client for a paired remote Hermes session."""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request


class HermesRemote:
    def __init__(self,base_url:str,login_token:str,timeout:float=300):
        self.base=base_url.rstrip("/");self.login_token=login_token;self.timeout=timeout

    def request(self,path:str,payload:dict|None=None):
        data=json.dumps(payload).encode() if payload is not None else None
        req=urllib.request.Request(self.base+path,data=data,
            headers={"Authorization":"Bearer "+self.login_token,
                     "Content-Type":"application/json","Accept":"application/json"},
            method="POST" if data is not None else "GET")
        with urllib.request.urlopen(req,timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def capabilities(self)->dict:
        return self.request("/v1/capabilities")

    def chat(self,text:str,previous_response_id:str|None=None)->tuple[str,str]:
        payload={"model":"hermes-agent","input":text,"store":True}
        if previous_response_id:payload["previous_response_id"]=previous_response_id
        result=self.request("/v1/responses",payload)
        response_id=str(result.get("id") or "")
        chunks=[]
        for item in result.get("output",[]):
            if not isinstance(item,dict) or item.get("type")!="message":continue
            for content in item.get("content",[]):
                if isinstance(content,dict) and content.get("type")=="output_text":
                    chunks.append(str(content.get("text") or ""))
        value="".join(chunks).strip()
        if not value:
            value=str(result.get("output_text") or result.get("response") or "").strip()
        if not response_id:raise RuntimeError("Hermes did not return a response id")
        if not value:value=json.dumps(result,indent=2)
        return response_id,value


def main(argv=None)->int:
    parser=argparse.ArgumentParser(prog="handai-hermes-remote")
    parser.add_argument("--url",default=os.environ.get("HERMES_REMOTE_URL"))
    args=parser.parse_args(argv)
    login_token=os.environ.get("HERMES_REMOTE_TOKEN","")
    if not args.url or not login_token:
        print("Hermes remote URL/login token is missing.");return 2
    client=HermesRemote(args.url,login_token)
    try:client.capabilities()
    except Exception as e:print(f"Connection failed: {e}");return 1
    print("HandAI connected to the remote Hermes session. /quit exits.")
    previous=None
    while True:
        try:text=input("you> ").strip()
        except (EOFError,KeyboardInterrupt):print();break
        if text in ("/quit","/exit"):break
        if not text:continue
        try:
            previous,response=client.chat(text,previous)
            print("hermes> "+response)
        except (OSError,urllib.error.HTTPError,ValueError,RuntimeError) as e:print(f"error> {e}")
    return 0


if __name__=="__main__":raise SystemExit(main())
