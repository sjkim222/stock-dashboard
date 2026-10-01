"""
시세·재무 데이터 계층.

- 실시간 현재가: 한국투자증권 API(KIS)가 설정되어 있으면 사용
- 차트·수익률·배당 등 재무 지표, 미국 지수: yfinance (키 불필요, 지연 시세)
"""
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import yfinance as yf

from themes import INDICES, yf_symbol

FX_FALLBACK = 1400.0  # 환율을 못 받아올 때 쓰는 원/달러 기본값


def usd_krw():
    try:
        h = yf.Ticker("KRW=X").history(period="5d")
        if not h.empty:
            return float(h["Close"].dropna().iloc[-1])
    except Exception:
        pass
    return FX_FALLBACK


def _yf_quote(symbol):
    """야후 기준 현재가와 전일 대비. 장중에는 약 15~20분 지연."""
    h = yf.Ticker(symbol).history(period="5d", interval="1d")
    closes = h["Close"].dropna()
    if closes.empty:
        raise ValueError(f"{symbol} 시세 없음")
    last = float(closes.iloc[-1])
    prev = float(closes.iloc[-2]) if len(closes) > 1 else last
    return {
        "price": last,
        "change": last - prev,
        "change_pct": (last / prev - 1) * 100 if prev else 0.0,
        "source": "Yahoo 지연",
    }


def index_quotes(kis=None):
    """국가별 대표지수 현재값. 국내 지수는 KIS가 있으면 실시간."""
    out = {}
    for country, items in INDICES.items():
        rows = []
        for it in items:
            q = None
            if kis and it.get("kis"):
                try:
                    q = kis.domestic_index(it["kis"])
                except Exception as e:  # 실패 시 야후로 대체
                    q = {"error": str(e)}
            if not q or "error" in q:
                try:
                    q = _yf_quote(it["yf"])
                except Exception as e:
                    q = {"price": None, "change": None, "change_pct": None, "source": f"오류: {e}"}
            rows.append({**it, **q})
        out[country] = rows
    return out


def history(symbol, period="1y", interval="1d"):
    cols = ["Open", "High", "Low", "Close", "Volume"]
    try:
        h = yf.Ticker(symbol).history(period=period, interval=interval)
    except Exception:
        return pd.DataFrame(columns=cols)
    if h.empty or "Close" not in h:
        return pd.DataFrame(columns=cols)
    return h[cols].dropna(subset=["Close"])


def live_quote(row, kis=None):
    """종목 한 개의 현재가 (KIS 실시간 → 실패하면 야후 지연)."""
    if kis:
        try:
            if row["country"] == "KR":
                return kis.domestic_price(row["code"])
            return kis.overseas_price(row["code"], row["market"])
        except Exception:
            pass
    return _yf_quote(yf_symbol(row))


# ---------------- 스크리너용 지표 ----------------
def _info(symbol):
    try:
        return yf.Ticker(symbol).info or {}
    except Exception:
        return {}


def _dividend_yield_pct(info, price):
    """배당수익률(%). yfinance 버전마다 dividendYield 단위가 달라 배당금/주가로 직접 계산."""
    rate = info.get("dividendRate") or info.get("trailingAnnualDividendRate")
    if rate and price:
        return float(rate) / float(price) * 100
    t = info.get("trailingAnnualDividendYield")
    return float(t) * 100 if t is not None else None


def _returns(closes: pd.Series):
    closes = closes.dropna()
    if len(closes) < 2:
        return {}
    last = closes.iloc[-1]

    def ret(days):
        if len(closes) <= days:
            return None
        return (last / closes.iloc[-1 - days] - 1) * 100

    high = closes.max()
    return {
        "ret_1m": ret(21),
        "ret_3m": ret(63),
        "ret_1y": (last / closes.iloc[0] - 1) * 100,
        "from_high_pct": (last / high - 1) * 100 if high else None,  # 52주 고점 대비 (0이면 신고가)
    }


def build_metrics(universe):
    """유니버스 전체 종목의 비교 지표 표를 만든다."""
    fx = usd_krw()
    symbols = [yf_symbol(r) for r in universe]

    # 1) 1년 종가를 한 번에 받아 수익률 계산
    try:
        px = yf.download(symbols, period="1y", interval="1d", auto_adjust=True,
                         progress=False, group_by="column", threads=True)["Close"]
        if isinstance(px, pd.Series):
            px = px.to_frame(symbols[0])
    except Exception:
        px = pd.DataFrame()

    # 2) PER·시가총액·배당 등 기업 정보 (병렬)
    with ThreadPoolExecutor(max_workers=8) as ex:
        infos = list(ex.map(_info, symbols))

    rows = []
    for r, sym, info in zip(universe, symbols, infos):
        closes = px[sym] if sym in getattr(px, "columns", []) else pd.Series(dtype=float)
        rets = _returns(closes)
        c = closes.dropna()
        day_chg = (c.iloc[-1] / c.iloc[-2] - 1) * 100 if len(c) > 1 else None
        price = c.iloc[-1] if not c.empty else info.get("currentPrice")
        mcap = info.get("marketCap")
        cur = info.get("currency") or ("KRW" if r["country"] == "KR" else "USD")
        mcap_krw = (mcap * fx if cur == "USD" else mcap) if mcap else None
        rows.append({
            "country": r["country"],
            "code": r["code"],
            "name": r["name"],
            "market": r["market"],
            "themes": ", ".join(r["themes"]),
            "price": float(price) if price is not None else None,
            "currency": cur,
            "change_pct": day_chg,
            "market_cap_jo": mcap_krw / 1e12 if mcap_krw else None,  # 원화 환산, 조 단위
            "per": info.get("trailingPE"),
            "fwd_per": info.get("forwardPE"),
            "pbr": info.get("priceToBook"),
            "div_yield": _dividend_yield_pct(info, price),
            "source": "Yahoo 지연",
            **rets,
        })

    return pd.DataFrame(rows), fx


def overlay_live(df: pd.DataFrame, kis) -> pd.DataFrame:
    """KIS 실시간 현재가(국내는 PER·PBR·시총 포함)로 표를 덮어쓴다."""
    if kis is None or df.empty:
        return df
    df = df.copy()
    for i, r in df.iterrows():
        try:
            q = live_quote(r, kis)
        except Exception:
            continue
        if q.get("source") != "KIS 실시간":
            continue
        df.at[i, "price"] = q.get("price")
        df.at[i, "change_pct"] = q.get("change_pct")
        df.at[i, "source"] = q.get("source")
        if r["country"] == "KR":
            if q.get("per"):
                df.at[i, "per"] = q["per"]
            if q.get("pbr"):
                df.at[i, "pbr"] = q["pbr"]
            if q.get("market_cap_krw"):
                df.at[i, "market_cap_jo"] = q["market_cap_krw"] / 1e12
    return df
