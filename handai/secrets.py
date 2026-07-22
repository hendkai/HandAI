"""Secret store - tokens for token-env providers.

Stored at $HANDAI_STATE/secrets.json with 0600 perms. This is deliberately NOT
presented as encryption: a headless handheld that boots straight into the
cockpit has no secret to derive a key from, so "encryption" without a boot
passphrase would be theatre. If the user opts into a boot PIN (see cockpit
settings), we wrap this file with it; until then it is plaintext + 0600 and the
threat model is "someone pulls the SD card", which we call out in the UI.
"""

from __future__ import annotations

import json
import os
import base64
import hashlib
import hmac
import secrets as random
from pathlib import Path


def _state_dir() -> Path:
    base = os.environ.get("HANDAI_STATE") or os.path.expanduser("~/.local/state/handai")
    p = Path(base)
    p.mkdir(parents=True, exist_ok=True)
    return p


class SecretStore:
    def __init__(self, path: Path | None = None):
        self.path = path or (_state_dir() / "secrets.json")
        self._data: dict[str, str] = {}
        self._encrypted=False;self._locked=False;self._salt=b"";self._keys:tuple[bytes,bytes]|None=None
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                raw=json.loads(self.path.read_text("utf-8"))
                if raw.get("_format")=="handai-secrets-v1":
                    self._encrypted=True;self._locked=True;self._salt=base64.b64decode(raw["salt"]);self._envelope=raw
                else:self._data = raw
            except (ValueError, OSError):
                self._data = {}

    def _flush(self) -> None:
        if self._locked:raise RuntimeError("secret store is locked")
        tmp = self.path.with_suffix(".tmp")
        output=self._seal() if self._encrypted else self._data
        tmp.write_text(json.dumps(output, indent=2), "utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass  # e.g. on Windows dev boxes; harmless
        tmp.replace(self.path)

    def get(self, provider_id: str) -> str | None:
        if self._locked:return None
        return self._data.get(provider_id)

    def set(self, provider_id: str, token: str) -> None:
        if self._locked:raise RuntimeError("secret store is locked")
        self._data[provider_id] = token
        self._flush()

    def clear(self, provider_id: str) -> None:
        if self._locked:raise RuntimeError("secret store is locked")
        self._data.pop(provider_id, None)
        self._flush()

    def has(self, provider_id: str) -> bool:
        return bool(self._data.get(provider_id))

    @property
    def locked(self)->bool:return self._locked

    @property
    def encrypted(self)->bool:return self._encrypted

    @staticmethod
    def _derive(pin:str,salt:bytes)->tuple[bytes,bytes]:
        key=hashlib.scrypt(pin.encode("utf-8"),salt=salt,n=2**14,r=8,p=1,dklen=64)
        return key[:32],key[32:]

    @staticmethod
    def _crypt(data:bytes,key:bytes,nonce:bytes)->bytes:
        out=bytearray()
        for counter in range((len(data)+31)//32):
            out.extend(hmac.new(key,nonce+counter.to_bytes(8,"big"),hashlib.sha256).digest())
        return bytes(a^b for a,b in zip(data,out))

    def _seal(self)->dict:
        assert self._keys
        nonce=random.token_bytes(16);plain=json.dumps(self._data,separators=(",",":")).encode()
        cipher=self._crypt(plain,self._keys[0],nonce)
        tag=hmac.new(self._keys[1],b"v1"+self._salt+nonce+cipher,hashlib.sha256).digest()
        b64=lambda value:base64.b64encode(value).decode("ascii")
        return {"_format":"handai-secrets-v1","salt":b64(self._salt),"nonce":b64(nonce),"ciphertext":b64(cipher),"tag":b64(tag)}

    def unlock(self,pin:str)->bool:
        if not self._locked:return True
        try:
            env=self._envelope;nonce=base64.b64decode(env["nonce"]);cipher=base64.b64decode(env["ciphertext"]);tag=base64.b64decode(env["tag"])
            keys=self._derive(pin,self._salt)
            expected=hmac.new(keys[1],b"v1"+self._salt+nonce+cipher,hashlib.sha256).digest()
            if not hmac.compare_digest(tag,expected):return False
            self._data=json.loads(self._crypt(cipher,keys[0],nonce).decode("utf-8"));self._keys=keys;self._locked=False
            return True
        except (ValueError,KeyError,UnicodeDecodeError,json.JSONDecodeError):return False

    def enable_pin(self,pin:str)->None:
        if self._locked:raise RuntimeError("secret store is locked")
        if len(pin)<4:raise ValueError("PIN needs at least 4 characters")
        self._salt=random.token_bytes(16);self._keys=self._derive(pin,self._salt);self._encrypted=True;self._flush()
