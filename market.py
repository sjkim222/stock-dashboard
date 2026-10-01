"""
시세·재무 데이터 계층.

한국투자증권 API(KIS) 키가 있으면 KIS가 주 데이터원이다.
  지수, 현재가, 차트, 수익률, PER·PBR·시총, 52주 고점 → KIS
  배당수익률 → 야후 (KIS 시세 API에 없음, 실패하면 빈칸)
키가 없으면 전부 야후(yfinance, 지연 시세)로 동작한다.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import pandas as pd
import yfinance as yf

from themes import INDICES, yf_symbol

FX_FALLBACK = 1400.0
OHLCV = ["Open", "High", "Low", "Close", "Volume"]
PERIOD_DAYS = {"5d": 7, "1mo": 31, "3mo": 92, "6mo": 183, "1y": 366, "5y": 1830}
_OVS_INDEX_CODE = {}  # 해외지수별로 실제 동작한 KIS 코드 기억


# ================= 야후 (예비) =================
def _yf_history(symbol, period="1y", interval="1d"):
    try:
        h = yf.Ticker(symbol).history(period=period, interval=interval)
    except Exception:
        return pd.DataFrame(columns=OHLCV)
    if h.empty or "Close" not in h:
        return pd.DataFrame(columns=OHLCV)
    h = h[OHLCV].dropna(subset=["Close"])
    h.index = pd.to_datetime(h.index).tz_localize(None)
    return h


def _yf_quote(symbol):
    closes = _yf_history(symbol, "5d")["Close"]
    if closes.empty:
        raise ValueError(f"{symbol} 시세 없음")
    last = float(closes.iloc[-1])
    prev = float(closes.iloc[-2]) if len(closes) > 1 else last
    return {"price": last, "change": last - prev,
            "change_pct": (last / prev - 1) * 100 if prev else 0.0, "source": "Yahoo 지연"}


def _yf_info(symbol):
    try:
        return yf.Ticker(symbol).info or {}
    except Exception:
        return {}


def usd_krw(kis=None):
    if kis:
        try:
            fx = kis.overseas_price("AAPL", "NAS").get("fx")
            if fx:
                return float(fx)
        except Exception:
            pass
    closes = _yf_history("KRW=X", "5d")["Close"]
    return float(closes.iloc[-1]) if not closes.empty else FX_FALLBACK


# ================= 지수 =================
def _kis_ovs_index(kis, it, fn):
    """해외지수 코드 후보를 차례로 시도하고, 성공한 코드를 기억한다."""
    codes = [_OVS_INDEX_CODE[it["name"]]] if it["name"] in _OVS_INDEX_CODE else it.get("kis_ovs", [])
    last_err = None
    for code in codes:
        try:
            out = fn(code)
            _OVS_INDEX_CODE[it["name"]] = code
            return out
        except Exception as e:
            last_err = e
    raise last_err or ValueError("코드 없음")


def index_quotes(kis=None):
    out = {}
    for country, items in INDICES.items():
        rows = []
        for it in items:
            q = None
            if kis:
                try:
                    if it.get("kis"):
                        q = kis.domestic_index(it["kis"])
                    elif it.get("kis_ovs"):
                        q = _kis_ovs_index(kis, it, kis.overseas_index)
                except Exception:
                    q = None
            if q is None:
                try:
                    q = _yf_quote(it["yf"])
                except Exception as e:
                    q = {"price": None, "change": None, "change_pct": None, "source": f"오류: {e}"}
            rows.append({**it, **q})
        out[country] = rows
    return out


def index_history(it, period="1y", kis=None):
    start, end, p = _range(period)
    if kis:
        try:
            if it.get("kis"):
                df = kis.domestic_index_chart(it["kis"], start, end, p)
            else:
                df = _kis_ovs_index(kis, it, lambda c: _nonempty(kis.overseas_index_chart(c, start, end, p)))
            if not df.empty:
                return df
        except Exception:
            pass
    return _yf_history(it["yf"], period, "1wk" if p == "W" else "1d")


# ================= 종목 =================
def live_quote(row, kis=None):
    if kis:
        try:
            if row["country"] == "KR":
                return kis.domestic_price(row["code"])
            return kis.overseas_price(row["code"], row.get("market", "NAS"))
        except Exception:
            pass
    return _yf_quote(yf_symbol(row))


def stock_history(row, period="1y", kis=None):
    start, end, p = _range(period)
    if kis:
        try:
            if row["country"] == "KR":
                df = kis.domestic_chart(row["code"], start, end, p)
            else:
                df = kis.overseas_chart(row["code"], row.get("market", "NAS"), start, p)
            if not df.empty:
                return df
        except Exception:
            pass
    return _yf_history(yf_symbol(row), period, "1wk" if p == "W" else "1d")


# ================= 스크리너 지표 =================
def _returns(closes: pd.Series, high_52w=None, per_month=21):
    """per_month: 한 달에 해당하는 봉 개수 (일봉 21, 주봉 4)"""
    closes = closes.dropna()
    if len(closes) < 2:
        return {}
    last = closes.iloc[-1]

    def ret(days):
        return (last / closes.iloc[-1 - days] - 1) * 100 if len(closes) > days else None

    # 증권사가 주는 52주 최고가는 종목에 따라 차트와 맞지 않는 값이 섞여 올 수 있어,
    # 차트 최고 종가보다 15% 넘게 높으면 믿지 않고 차트 값을 쓴다
    high = closes.max()
    if high_52w and high_52w <= high * 1.15:
        high = max(high, high_52w)
    return {
        "ret_1m": ret(per_month),
        "ret_3m": ret(per_month * 3),
        "ret_1y": (last / closes.iloc[0] - 1) * 100,
        "from_high_pct": (last / high - 1) * 100 if high else None,
    }


def _div_yield(info, price):
    """배당수익률(%). yfinance 버전마다 단위가 달라 배당금/주가로 계산."""
    rate = info.get("dividendRate") or info.get("trailingAnnualDividendRate")
    if rate and price:
        return float(rate) / float(price) * 100
    t = info.get("trailingAnnualDividendYield")
    return float(t) * 100 if t is not None else None


def _metric_row(r, fx, kis):
    """한 종목의 비교 지표. KIS가 있으면 KIS, 없거나 실패하면 야후."""
    base = {"country": r["country"], "code": r["code"], "name": r["name"],
            "market": r["market"], "themes": ", ".join(r["themes"]),
            "currency": "KRW" if r["country"] == "KR" else "USD"}
    sym = yf_symbol(r)

    if kis:
        try:
            q = live_quote(r, kis)
            if q.get("source") == "KIS 실시간":
                # 수익률 계산용으로는 1년 주봉이면 충분 → 종목당 1회 호출
                start, end, _ = _range("1y")
                if r["country"] == "KR":
                    wk = kis.domestic_chart(r["code"], start, end, "W")
                else:
                    wk = kis.overseas_chart(r["code"], q.get("excd") or r["market"], start, "W")
                closes = wk["Close"]
                mcap = q.get("market_cap_krw") or (q["market_cap_usd"] * fx if q.get("market_cap_usd") else None)
                return {**base, "price": q["price"], "change_pct": q.get("change_pct"),
                        "market_cap_jo": mcap / 1e12 if mcap else None,
                        "per": q.get("per") or None, "fwd_per": None, "pbr": q.get("pbr") or None,
                        "div_yield": None, "source": "KIS 실시간",
                        **_returns(closes, q.get("high_52w"), per_month=4)}
        except Exception:
            pass

    # 야후 경로
    info = _yf_info(sym)
    closes = _yf_history(sym, "1y")["Close"]
    c = closes.dropna()
    price = float(c.iloc[-1]) if not c.empty else info.get("currentPrice")
    mcap = info.get("marketCap")
    cur = info.get("currency") or base["currency"]
    mcap_krw = (mcap * fx if cur == "USD" else mcap) if mcap else None
    return {**base, "price": price,
            "change_pct": (c.iloc[-1] / c.iloc[-2] - 1) * 100 if len(c) > 1 else None,
            "market_cap_jo": mcap_krw / 1e12 if mcap_krw else None,
            "per": info.get("trailingPE"), "fwd_per": info.get("forwardPE"),
            "pbr": info.get("priceToBook"), "div_yield": _div_yield(info, price),
            "source": "Yahoo 지연", **_returns(closes)}


def dividend_growth(symbol, this_year=None):
    """배당 성장 지표 (야후 배당 이력 기준, 올해처럼 끝나지 않은 해는 제외).
    반환: div_cagr_5y(5년 연평균 배당 성장률 %), div_cagr_3y, div_up_years(연속 배당 증가 연수)"""
    out = {"div_cagr_5y": None, "div_cagr_3y": None, "div_up_years": None}
    try:
        d = yf.Ticker(symbol).dividends
    except Exception:
        return out
    if d is None or len(d) == 0:
        return out
    d.index = pd.to_datetime(d.index).tz_localize(None) if getattr(d.index, "tz", None) else pd.to_datetime(d.index)
    this_year = this_year or date.today().year
    yearly = d.groupby(d.index.year).sum()
    yearly = yearly[yearly.index < this_year]
    if yearly.empty:
        return out
    last = this_year - 1

    def cagr(n):
        a, b = yearly.get(last - n), yearly.get(last)
        if a and b and a > 0 and b > 0:
            return ((b / a) ** (1 / n) - 1) * 100
        return None

    out["div_cagr_5y"] = cagr(5)
    out["div_cagr_3y"] = cagr(3)
    # 직전 해부터 거꾸로 세며 배당이 늘어난 해가 몇 년 이어졌는지
    up, y = 0, last
    while yearly.get(y) and yearly.get(y - 1) and yearly[y] > yearly[y - 1] * 1.001:
        up += 1
        y -= 1
    out["div_up_years"] = up if yearly.get(last) else 0
    return out


def build_metrics(universe, kis=None):
    fx = usd_krw(kis)
    # KIS 호출은 내부에서 간격 조절되므로 동시 작업 수만 적당히
    with ThreadPoolExecutor(max_workers=4 if kis else 8) as ex:
        rows = list(ex.map(lambda r: _metric_row(r, fx, kis), universe))
    df = pd.DataFrame(rows)

    # KIS 경로 종목의 배당률은 야후에서 보충 (실패해도 무시)
    if kis and not df.empty:
        need = df.index[df["div_yield"].isna()]
        syms = [yf_symbol(universe[i]) for i in need]
        with ThreadPoolExecutor(max_workers=8) as ex:
            infos = list(ex.map(_yf_info, syms))
        for i, info in zip(need, infos):
            df.at[i, "div_yield"] = _div_yield(info, df.at[i, "price"]) if info else None

    # 배당 성장 지표 (야후 배당 이력)
    if not df.empty:
        with ThreadPoolExecutor(max_workers=8) as ex:
            growth = list(ex.map(lambda r: dividend_growth(yf_symbol(r)), universe))
        g = pd.DataFrame(growth, index=df.index)
        for col in g.columns:
            df[col] = g[col]
    return df, fx


# ================= 도우미 =================
def _range(period):
    end = date.today()
    start = end - timedelta(days=PERIOD_DAYS.get(period, 366))
    return start, end, ("W" if period == "5y" else "D")


def _nonempty(df):
    if df.empty:
        raise ValueError("빈 응답")
    return df


def refresh_prices(df, kis):
    """스크리너 표의 현재가·등락률만 실시간으로 갱신 (지표는 그대로)."""
    if kis is None or df.empty:
        return df
    df = df.copy()

    def one(i):
        try:
            return i, live_quote(df.loc[i].to_dict(), kis)
        except Exception:
            return i, None

    with ThreadPoolExecutor(max_workers=4) as ex:
        for i, q in ex.map(one, df.index):
            if q and q.get("source") == "KIS 실시간":
                df.at[i, "price"] = q.get("price")
                df.at[i, "change_pct"] = q.get("change_pct")
                df.at[i, "source"] = "KIS 실시간"
    return df
