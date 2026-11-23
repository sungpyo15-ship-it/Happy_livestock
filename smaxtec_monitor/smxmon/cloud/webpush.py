"""웹 푸시 발송 (RFC 8291 aes128gcm 암호화 + RFC 8292 VAPID) — cryptography 만 사용, 추가 설치 없음.

받는 쪽: 홈 화면에 추가한 앱(아이폰 iOS 16.4+ / 안드로이드 크롬)이 '알림 받기'로 만든 구독(endpoint·p256dh·auth).
보내는 곳: 구글(FCM)·애플·모질라 푸시 서버 — 가입·비용 없음.
"""
from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from cryptography.hazmat.primitives import hashes, hmac, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def unb64u(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _pub_bytes(key: ec.EllipticCurvePublicKey) -> bytes:
    return key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def _hmac(key: bytes, data: bytes) -> bytes:
    h = hmac.HMAC(key, hashes.SHA256())
    h.update(data)
    return h.finalize()


class Vapid:
    """서버 고유 키(처음 한 번 만들어 <데이터 폴더>/vapid.pem 에 보관)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        if self.path.exists():
            self.key = serialization.load_pem_private_key(self.path.read_bytes(), None)
        else:
            self.key = ec.generate_private_key(ec.SECP256R1())
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_bytes(self.key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                         serialization.NoEncryption()))
            os.chmod(self.path, 0o600)
        self.public_b64 = b64u(_pub_bytes(self.key.public_key()))

    def header(self, endpoint: str, sub: str) -> str:
        u = urlparse(endpoint)
        claims = {"aud": f"{u.scheme}://{u.netloc}", "exp": int(time.time()) + 12 * 3600, "sub": sub}
        signing = (b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode()) + "." +
                   b64u(json.dumps(claims, separators=(",", ":")).encode()))
        r, s = decode_dss_signature(self.key.sign(signing.encode(), ec.ECDSA(hashes.SHA256())))
        jwt = signing + "." + b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
        return f"vapid t={jwt}, k={self.public_b64}"


def encrypt(payload: bytes, p256dh: str, auth: str, *, _eph: ec.EllipticCurvePrivateKey | None = None,
            _salt: bytes | None = None) -> bytes:
    """RFC 8291 — 받는 기기만 풀 수 있게 암호화한 본문(aes128gcm)."""
    ua_pub_b = unb64u(p256dh)
    auth_b = unb64u(auth)
    ua_pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_pub_b)
    eph = _eph or ec.generate_private_key(ec.SECP256R1())
    as_pub_b = _pub_bytes(eph.public_key())
    secret = eph.exchange(ec.ECDH(), ua_pub)
    prk_key = _hmac(auth_b, secret)
    ikm = _hmac(prk_key, b"WebPush: info\x00" + ua_pub_b + as_pub_b + b"\x01")
    salt = _salt or os.urandom(16)
    prk = _hmac(salt, ikm)
    cek = _hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = _hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    ct = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    return salt + (4096).to_bytes(4, "big") + bytes([len(as_pub_b)]) + as_pub_b + ct


def decrypt(body: bytes, ua_priv: ec.EllipticCurvePrivateKey, auth: bytes) -> bytes:
    """시험용(받는 쪽 동작 재현)."""
    salt, idlen = body[:16], body[20]
    as_pub_b = body[21:21 + idlen]
    ua_pub_b = _pub_bytes(ua_priv.public_key())
    secret = ua_priv.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_pub_b))
    ikm = _hmac(_hmac(auth, secret), b"WebPush: info\x00" + ua_pub_b + as_pub_b + b"\x01")
    prk = _hmac(salt, ikm)
    cek = _hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = _hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    pt = AESGCM(cek).decrypt(nonce, body[21 + idlen:], None)
    return pt.rstrip(b"\x00")[:-1]


def send(vapid: Vapid, sub: dict[str, Any], data: dict[str, Any], *, contact: str, ttl: int = 6 * 3600,
         timeout: float = 15) -> tuple[int, str]:
    """반환: (HTTP 상태, 설명). 404·410 = 구독 만료(지워야 함)."""
    endpoint = sub["endpoint"]
    if not endpoint.startswith("https://"):
        return 0, "잘못된 주소"
    body = encrypt(json.dumps(data, ensure_ascii=False).encode(), sub["p256dh"], sub["auth"])
    req = urllib.request.Request(endpoint, data=body, method="POST", headers={
        "Authorization": vapid.header(endpoint, contact), "Content-Encoding": "aes128gcm",
        "Content-Type": "application/octet-stream", "TTL": str(ttl), "Urgency": "high"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, "ok"
    except urllib.error.HTTPError as e:
        return e.code, (e.read() or b"")[:200].decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return 0, f"연결 실패: {e}"
