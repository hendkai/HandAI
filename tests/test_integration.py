"""Local integration tests for network-facing HandAI components."""

import json
import threading
import urllib.error
import urllib.parse
import urllib.request
import unittest
from http.cookiejar import CookieJar
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from unittest.mock import patch

from handai.hermes_remote import HermesRemote
from handai.phone import PhoneKeyboard
from handai.tmux import SessionInfo


class _HermesHandler(BaseHTTPRequestHandler):
    def log_message(self,*_):pass
    def _reply(self,payload,status=200):
        body=json.dumps(payload).encode();self.send_response(status)
        self.send_header("Content-Type","application/json");self.send_header("Content-Length",str(len(body)))
        self.end_headers();self.wfile.write(body)
    def do_POST(self):
        if self.headers.get("Authorization")!="Bearer test-key":self._reply({"error":"unauthorized"},401);return
        length=int(self.headers.get("Content-Length","0"));body=json.loads(self.rfile.read(length) or b"{}")
        if self.path=="/v1/responses":
            self._reply({"id":"response-1","output":[{"type":"message","content":[
                {"type":"output_text","text":"echo: "+body["input"]}
            ]}]})
        else:self._reply({"error":"missing"},404)
    def do_GET(self):
        if self.headers.get("Authorization")!="Bearer test-key":self._reply({"error":"unauthorized"},401);return
        if self.path=="/v1/capabilities":self._reply({"features":{"responses_api":True}})
        else:self._reply({"error":"missing"},404)


class TestHermesRemoteIntegration(unittest.TestCase):
    def test_session_and_chat_over_http(self):
        server=ThreadingHTTPServer(("127.0.0.1",0),_HermesHandler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            client=HermesRemote(f"http://127.0.0.1:{server.server_address[1]}","test-key")
            self.assertTrue(client.capabilities()["features"]["responses_api"])
            response_id,text=client.chat("hello")
            self.assertEqual(response_id,"response-1")
            self.assertEqual(text,"echo: hello")
        finally:server.shutdown();server.server_close();thread.join(timeout=2)


class TestPhonePairingIntegration(unittest.TestCase):
    @patch("handai.phone.send_session_text",return_value=(True,"sent"))
    def test_one_time_pair_cookie_and_csrf(self,_send):
        bridge=PhoneKeyboard(SessionInfo("handai-test",1,False,None),"127.0.0.1",lifetime=20).start()
        jar=CookieJar();opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        try:
            page=opener.open(bridge.url,timeout=2).read().decode()
            self.assertNotIn(bridge.token,page)
            self.assertTrue(any(cookie.name=="handai_pair" for cookie in jar))
            csrf=page.split("name=csrf value='",1)[1].split("'",1)[0]
            data=urllib.parse.urlencode({"csrf":csrf,"text":"hello","enter":"on"}).encode()
            response=opener.open(urllib.request.Request(f"http://127.0.0.1:{bridge.port}/send",data=data),timeout=2)
            self.assertEqual(response.status,200)
            # The QR pairing URL is single-use; a fresh browser cannot replay it.
            with self.assertRaises(urllib.error.HTTPError):urllib.request.urlopen(bridge.url,timeout=2)
        finally:bridge.stop()


if __name__=="__main__":unittest.main()
