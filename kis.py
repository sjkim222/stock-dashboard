"""
한국투자증권(KIS) Open API 클라이언트 — 실시간(현재가) 시세 조회용.

필요한 것: 한국투자증권 계좌 + KIS Developers에서 발급한 APP KEY / APP SECRET
사양 출처: https://github.com/koreainvestment/open-trading-api
"""
import json
import time
from pathlib import Path

import requests

REAL_URL = "https://openapi.koreainvestment.com:9443"
DEMO_URL = "https://openapivts.koreainvestment.com:29443"
TOKEN_FILE = Path(__file__).parent / ".kis_token.json"


class KISError(RuntimeError):
    pass


class KISClient:
    def __init__(self, app_key: str, app_secret: str, demo: bool = False):
        self.app_key = app_key
        self.app_secret = app_secret
        self.base = DEMO_URL if demo else REAL_URL
        self._token = None
        self._last_call = 0.0

    # ---------- 인증 ----------
    def _load_cached_token(self):
        """접근토큰은 24시간 유효하고 재발급 횟수 제한이 있어 파일에 저장해 재사용한다."""
        try:
            data = json.loads(TOKEN_FILE.read_text())
            if data.get("app_key") == self.app_key and data.get("expires_at", 0) > time.time() + 600:
                return data["token"]
        except Exception:
            pass
        return None

    def token(self):
        if self._token:
            return self._token
        cached = self._load_cached_token()
        if cached:
            self._token = cached
            return cached
        res = requests.post(
            f"{self.base}/oauth2/tokenP",
            json={
                "grant_type": "client_credentials",
                "appkey": self.app_key,
                "appsecret": self.app_secret,
            },
            timeout=10,
        )
        body = res.json()
        if "access_token" not in body:
            raise KISError(f"토큰 발급 실패: {body}")
        self._token = body["access_token"]
        expires_in = int(body.get("expires_in", 86400))
        try:
            TOKEN_FILE.write_text(
                json.dumps(
                    {
                        "app_key": self.app_key,
                        "token": self._token,
                        "expires_at": time.time() + expires_in,
                    }
                )
            )
        except Exception:
            pass
        return self._token

    # ---------- 공통 호출 ----------
    def _get(self, path: str, tr_id: str, params: dict) -> dict:
        # 초당 호출 제한을 넘지 않도록 간격 유지
        wait = 0.06 - (time.time() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        headers = {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self.token()}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id,
            "custtype": "P",
        }
        res = requests.get(f"{self.base}{path}", headers=headers, params=params, timeout=10)
        self._last_call = time.time()
        body = res.json()
        if body.get("rt_cd") != "0":
            raise KISError(f'{tr_id} 오류: {body.get("msg1", body)}')
        return body.get("output", {})

    # ---------- 시세 ----------
    def domestic_price(self, code: str) -> dict:
        """국내 주식 현재가 [v1_국내주식-008]"""
        o = self._get(
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            "FHKST01010100",
            {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code},
        )
        return {
            "price": _num(o.get("stck_prpr")),
            "change": _signed(o.get("prdy_vrss"), o.get("prdy_vrss_sign")),
            "change_pct": _signed(o.get("prdy_ctrt"), o.get("prdy_vrss_sign")),
            "per": _num(o.get("per")),
            "pbr": _num(o.get("pbr")),
            # hts_avls 단위: 억원
            "market_cap_krw": _num(o.get("hts_avls")) * 1e8 if o.get("hts_avls") else None,
            "source": "KIS 실시간",
        }

    def domestic_index(self, index_code: str) -> dict:
        """국내 업종지수 현재가 (0001 코스피, 1001 코스닥, 2001 코스피200)"""
        o = self._get(
            "/uapi/domestic-stock/v1/quotations/inquire-index-price",
            "FHPUP02100000",
            {"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": index_code},
        )
        return {
            "price": _num(o.get("bstp_nmix_prpr")),
            "change": _signed(o.get("bstp_nmix_prdy_vrss"), o.get("prdy_vrss_sign")),
            "change_pct": _signed(o.get("bstp_nmix_prdy_ctrt"), o.get("prdy_vrss_sign")),
            "source": "KIS 실시간",
        }

    def overseas_price(self, symbol: str, excd: str) -> dict:
        """해외 주식 현재가. 거래소가 틀리면 빈 값이 오므로 다른 거래소로 재시도한다."""
        for ex in [excd] + [e for e in ("NAS", "NYS", "AMS") if e != excd]:
            o = self._get(
                "/uapi/overseas-price/v1/quotations/price",
                "HHDFS00000300",
                {"AUTH": "", "EXCD": ex, "SYMB": symbol},
            )
            if o and o.get("last") not in (None, "", "0", "0.0000"):
                return {
                    "price": _num(o.get("last")),
                    "change": _signed(o.get("diff"), o.get("sign")),
                    "change_pct": _signed(o.get("rate"), o.get("sign")),
                    "source": "KIS 실시간",
                }
        raise KISError(f"{symbol} 해외 시세 없음")


def _num(v):
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _signed(value, sign_code):
    """KIS 전일대비 부호(1·2 상승, 3 보합, 4·5 하락)를 값에 반영한다."""
    v = _num(value)
    if v is None:
        return None
    if str(sign_code) in ("4", "5"):
        return -abs(v)
    if str(sign_code) in ("1", "2"):
        return abs(v)
    return v
