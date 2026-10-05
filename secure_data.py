"""
증권사(KIS) 기반 사전 계산 파일의 암호화/복호화.

공개 저장소의 data 브랜치에 올라가는 파일이라, 증권사 데이터는 암호화해서 저장한다.
암호 키는 KIS_APP_SECRET에서 만들어지므로 별도 비밀값이 필요 없다.
(GitHub Actions와 Streamlit 양쪽 Secrets에 이미 같은 KIS_APP_SECRET이 있음)
키를 재발급하면 다음 사전 계산부터 새 키로 암호화된다.
"""
import base64
import hashlib
import json

from cryptography.fernet import Fernet, InvalidToken

_CONTEXT = "stock-dashboard/kis-metrics/v1|"


def _fernet(secret: str) -> Fernet:
    digest = hashlib.sha256((_CONTEXT + secret).encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_payload(secret: str, meta: dict, csv_text: str) -> bytes:
    data = json.dumps({"meta": meta, "csv": csv_text}, ensure_ascii=False).encode("utf-8")
    return _fernet(secret).encrypt(data)


def decrypt_payload(secret: str, token: bytes):
    """(meta, csv_text). 키가 다르거나 파일이 손상되면 ValueError."""
    try:
        data = _fernet(secret).decrypt(token)
    except InvalidToken as e:
        raise ValueError("복호화 실패 (키가 바뀌었거나 파일이 손상됨)") from e
    obj = json.loads(data.decode("utf-8"))
    return obj["meta"], obj["csv"]
