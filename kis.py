"""
한국투자증권(KIS) Open API 클라이언트.

키가 있으면 이 앱의 주 데이터원으로 쓰인다.
  - 국내: 현재가·PER·PBR·시총, 업종지수, 일/주/월 차트
  - 미국: 현재가, 상세시세(PER·PBR·시총·52주), 일/주/월 차트, 해외지수
필요한 것: 한국투자증권 계좌 + KIS Developers APP KEY / APP SECRET
사양 출처: https://github.com/koreainvestment/open-trading-api
"""
import json
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

REAL_URL = "https://openapi.koreainvestment.com:9443"
DEMO_URL = "https://openapivts.koreainvestment.com:29443"
TOKEN_FILE = Path(__file__).parent / ".kis_token.json"
OHLCV = ["Open", "High", "Low", "Close", "Volume"]


class KISError(RuntimeError):
    pass


class KISClient:
    def __init__(self, app_key: str, app_secret: str, demo: bool = False):
        self.app_key = app_key
        self.app_secret = app_secret
        self.base = DEMO_URL if demo else REAL_URL
        # 모의투자는 초당 호출 한도가 훨씬 낮다
        self.min_gap = 0.55 if demo else 0.07
        self._token = None
        self._last_call = 0.0
        self._lock = threading.Lock()

    # ---------- 인증 ----------
    def _load_cached_token(self):
        """접근토큰은 하루 유효하고 재발급 빈도 제한이 있어 파일에 저장해 재사용한다."""
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
            json={"grant_type": "client_credentials", "appkey": self.app_key, "appsecret": self.app_secret},
            timeout=10,
        )
        body = res.json()
        if "access_token" not in body:
            raise KISError(f"토큰 발급 실패: {body.get('error_description') or body}")
        self._token = body["access_token"]
        try:
            TOKEN_FILE.write_text(json.dumps({
                "app_key": self.app_key,
                "token": self._token,
                "expires_at": time.time() + int(body.get("expires_in", 86400)),
            }))
        except Exception:
            pass
        return self._token

    # ---------- 공통 호출 ----------
    def _call(self, path: str, tr_id: str, params: dict, retry: int = 2) -> dict:
        """응답 전체(body)를 돌려준다. 호출 간격을 지켜 초당 한도를 넘지 않게 한다."""
        for attempt in range(retry + 1):
            with self._lock:
                wait = self.min_gap - (time.time() - self._last_call)
                if wait > 0:
                    time.sleep(wait)
                self._last_call = time.time()
            headers = {
                "content-type": "application/json; charset=utf-8",
                "authorization": f"Bearer {self.token()}",
                "appkey": self.app_key,
                "appsecret": self.app_secret,
                "tr_id": tr_id,
                "custtype": "P",
            }
            res = requests.get(f"{self.base}{path}", headers=headers, params=params, timeout=10)
            body = res.json()
            if body.get("rt_cd") == "0":
                return body
            msg = str(body.get("msg1", ""))
            # 초당 거래건수 초과는 잠깐 쉬고 재시도
            if "초당" in msg and attempt < retry:
                time.sleep(1.0)
                continue
            raise KISError(f"{tr_id} 오류: {msg or body}")
        raise KISError(f"{tr_id} 재시도 초과")

    # ================= 현재가 =================
    def domestic_price(self, code: str) -> dict:
        """국내 주식 현재가 [v1_국내주식-008]"""
        o = self._call("/uapi/domestic-stock/v1/quotations/inquire-price", "FHKST01010100",
                       {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code}).get("output", {})
        sign = o.get("prdy_vrss_sign")
        return {
            "price": _num(o.get("stck_prpr")),
            "change": _signed(o.get("prdy_vrss"), sign),
            "change_pct": _signed(o.get("prdy_ctrt"), sign),
            "per": _num(o.get("per")),
            "pbr": _num(o.get("pbr")),
            "market_cap_krw": _num(o.get("hts_avls")) * 1e8 if _num(o.get("hts_avls")) else None,  # 억원
            "high_52w": _num(o.get("w52_hgpr")),
            "source": "KIS 실시간",
        }

    def domestic_index(self, index_code: str) -> dict:
        """국내 업종지수 현재가 (0001 코스피, 1001 코스닥, 2001 코스피200)"""
        o = self._call("/uapi/domestic-stock/v1/quotations/inquire-index-price", "FHPUP02100000",
                       {"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": index_code}).get("output", {})
        sign = o.get("prdy_vrss_sign")
        return {
            "price": _num(o.get("bstp_nmix_prpr")),
            "change": _signed(o.get("bstp_nmix_prdy_vrss"), sign),
            "change_pct": _signed(o.get("bstp_nmix_prdy_ctrt"), sign),
            "source": "KIS 실시간",
        }

    def overseas_price(self, symbol: str, excd: str) -> dict:
        """해외 주식 상세시세 (현재가 + PER·PBR·시총·52주). 거래소가 틀리면 다른 거래소로 재시도."""
        for ex in _exchanges(excd):
            o = self._call("/uapi/overseas-price/v1/quotations/price-detail", "HHDFS76200200",
                           {"AUTH": "", "EXCD": ex, "SYMB": symbol}).get("output", {})
            last = _num(o.get("last"))
            if not last:
                continue
            base = _num(o.get("base"))  # 전일 종가
            chg = last - base if base else None
            return {
                "price": last,
                "change": chg,
                "change_pct": chg / base * 100 if base else None,
                "per": _num(o.get("perx")),
                "pbr": _num(o.get("pbrx")),
                "market_cap_usd": _usd_mcap(_num(o.get("tomv"))),
                "high_52w": _num(o.get("h52p")),
                "fx": _num(o.get("t_rate")),  # 당일 원/달러 환율
                "excd": ex,
                "source": "KIS 실시간",
            }
        raise KISError(f"{symbol} 해외 시세 없음")

    def overseas_index(self, code: str) -> dict:
        """해외지수 현재값 (.DJI, SPX 등) — 기간별시세 API의 output1 사용"""
        end = date.today()
        body = self._call("/uapi/overseas-price/v1/quotations/inquire-daily-chartprice", "FHKST03030100", {
            "FID_COND_MRKT_DIV_CODE": "N", "FID_INPUT_ISCD": code,
            "FID_INPUT_DATE_1": _ymd(end - timedelta(days=10)), "FID_INPUT_DATE_2": _ymd(end),
            "FID_PERIOD_DIV_CODE": "D",
        })
        o = body.get("output1", {}) or {}
        price = _num(o.get("ovrs_nmix_prpr"))
        if not price:
            raise KISError(f"해외지수 {code} 값 없음")
        sign = o.get("prdy_vrss_sign")
        return {
            "price": price,
            "change": _signed(o.get("ovrs_nmix_prdy_vrss"), sign),
            "change_pct": _signed(o.get("prdy_ctrt"), sign),
            "source": "KIS 실시간",
        }

    # ================= 기간별 시세 (차트) =================
    def domestic_chart(self, code: str, start: date, end: date, period: str = "D") -> pd.DataFrame:
        """국내 주식 일/주/월봉 [v1_국내주식-016] — 한 번에 최대 100개라 기간을 나눠 받는다."""
        rows = self._range_fetch(
            start, end, _window_days(period),
            lambda s, e: self._call(
                "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice", "FHKST03010100", {
                    "FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code,
                    "FID_INPUT_DATE_1": _ymd(s), "FID_INPUT_DATE_2": _ymd(e),
                    "FID_PERIOD_DIV_CODE": period, "FID_ORG_ADJ_PRC": "0",
                }).get("output2", []),
        )
        return _frame(rows, "stck_bsop_date",
                      {"stck_oprc": "Open", "stck_hgpr": "High", "stck_lwpr": "Low",
                       "stck_clpr": "Close", "acml_vol": "Volume"})

    def domestic_index_chart(self, index_code: str, start: date, end: date, period: str = "D") -> pd.DataFrame:
        """국내 업종지수 기간별 시세"""
        rows = self._range_fetch(
            start, end, _window_days(period) // 2,
            lambda s, e: self._call(
                "/uapi/domestic-stock/v1/quotations/inquire-daily-indexchartprice", "FHKUP03500100", {
                    "FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": index_code,
                    "FID_INPUT_DATE_1": _ymd(s), "FID_INPUT_DATE_2": _ymd(e),
                    "FID_PERIOD_DIV_CODE": period,
                }).get("output2", []),
        )
        return _frame(rows, "stck_bsop_date",
                      {"bstp_nmix_oprc": "Open", "bstp_nmix_hgpr": "High", "bstp_nmix_lwpr": "Low",
                       "bstp_nmix_prpr": "Close", "acml_vol": "Volume"})

    def overseas_index_chart(self, code: str, start: date, end: date, period: str = "D") -> pd.DataFrame:
        """해외지수 기간별 시세"""
        rows = self._range_fetch(
            start, end, _window_days(period),
            lambda s, e: self._call(
                "/uapi/overseas-price/v1/quotations/inquire-daily-chartprice", "FHKST03030100", {
                    "FID_COND_MRKT_DIV_CODE": "N", "FID_INPUT_ISCD": code,
                    "FID_INPUT_DATE_1": _ymd(s), "FID_INPUT_DATE_2": _ymd(e),
                    "FID_PERIOD_DIV_CODE": period,
                }).get("output2", []),
        )
        return _frame(rows, "stck_bsop_date",
                      {"ovrs_nmix_oprc": "Open", "ovrs_nmix_hgpr": "High", "ovrs_nmix_lwpr": "Low",
                       "ovrs_nmix_prpr": "Close", "acml_vol": "Volume"})

    def overseas_chart(self, symbol: str, excd: str, start: date, period: str = "D") -> pd.DataFrame:
        """해외 주식 기간별 시세 [v1_해외주식-010] — 기준일부터 과거로 약 100개씩 받는다."""
        gubn = {"D": "0", "W": "1", "M": "2"}[period]
        for ex in _exchanges(excd):
            rows, bymd = [], ""
            for _ in range(40):  # 안전장치
                out = self._call("/uapi/overseas-price/v1/quotations/dailyprice", "HHDFS76240000", {
                    "AUTH": "", "EXCD": ex, "SYMB": symbol, "GUBN": gubn, "BYMD": bymd, "MODP": "1",
                }).get("output2", []) or []
                out = [r for r in out if r.get("xymd")]
                if not out:
                    break
                rows.extend(out)
                oldest = min(r["xymd"] for r in out)
                if datetime.strptime(oldest, "%Y%m%d").date() <= start or len(out) < 2:
                    break
                bymd = _ymd(datetime.strptime(oldest, "%Y%m%d").date() - timedelta(days=1))
            if rows:
                df = _frame(rows, "xymd", {"open": "Open", "high": "High", "low": "Low",
                                           "clos": "Close", "tvol": "Volume"})
                return df[df.index >= pd.Timestamp(start)]
        return pd.DataFrame(columns=OHLCV)

    # ---------- 내부 ----------
    @staticmethod
    def _range_fetch(start, end, window_days, fetch):
        """[start, end] 기간을 window_days 단위로 나눠 최신 구간부터 받는다."""
        rows, e = [], end
        while e >= start:
            s = max(start, e - timedelta(days=window_days))
            rows.extend([r for r in (fetch(s, e) or []) if r])
            e = s - timedelta(days=1)
        return rows


# ================= 도우미 =================
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


def _usd_mcap(v):
    """해외 상세시세 시가총액(tomv). 백만 달러 단위로 오는 경우를 감지해 달러로 맞춘다."""
    if not v:
        return None
    return v * 1e6 if v < 1e7 else v


def _exchanges(first):
    return [first] + [e for e in ("NAS", "NYS", "AMS") if e != first]


def _ymd(d):
    return d.strftime("%Y%m%d")


def _window_days(period):
    # 한 번 호출에 100개 이하가 되도록 하는 달력 기준 기간
    return {"D": 135, "W": 650, "M": 2900}.get(period, 135)


def _frame(rows, date_key, mapping):
    if not rows:
        return pd.DataFrame(columns=OHLCV)
    df = pd.DataFrame(rows)
    df = df[df[date_key].astype(str).str.len() == 8]
    df.index = pd.to_datetime(df[date_key].astype(str), format="%Y%m%d")
    df = df.rename(columns=mapping)
    for c in OHLCV:
        df[c] = pd.to_numeric(df.get(c), errors="coerce")
    df = df[OHLCV]
    df = df[~df.index.duplicated()].sort_index()
    return df[df["Close"] > 0]
