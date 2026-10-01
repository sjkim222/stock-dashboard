"""
나라별 대표지수 + 조건별 종목 찾기 대시보드

실행:  streamlit run app.py
"""
import io
import threading
import time

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

import market
import nl_parser
from kis import KISClient
from screener import apply_filters, describe
from themes import INDICES, THEMES, build_universe

st.set_page_config(page_title="글로벌 주식 대시보드", page_icon="📈", layout="wide")


# ---------------- 설정 ----------------
def secret(key, default=None):
    try:
        return st.secrets.get(key, default)
    except Exception:  # secrets.toml이 없을 때
        return default


@st.cache_resource
def get_kis(key, sec, demo):
    # 키 값이 인자라서 Secrets를 바꾸면 새 클라이언트가 만들어진다
    return KISClient(key, sec, demo=demo)


KIS_KEY = str(secret("KIS_APP_KEY", "") or "").strip()
KIS_SECRET = str(secret("KIS_APP_SECRET", "") or "").strip()
KIS_DEMO = str(secret("KIS_DEMO", "false")).strip().lower() == "true"
HAS_KEYS = bool(KIS_KEY and KIS_SECRET)

st.sidebar.header("⚙️ 데이터 설정")
USE_KIS = st.sidebar.toggle(
    "한국투자증권 API 사용", value=HAS_KEYS, disabled=not HAS_KEYS, key="use_kis",
    help="끄면 야후(지연 시세)로 바뀝니다. 이 브라우저 화면에만 적용되고, 다른 방문자에게는 영향이 없습니다."
         if HAS_KEYS else "Secrets에 KIS_APP_KEY / KIS_APP_SECRET을 넣으면 켤 수 있습니다.")
KIS = get_kis(KIS_KEY, KIS_SECRET, KIS_DEMO) if HAS_KEYS and USE_KIS else None
# 캐시 구분용 꼬리표: 키가 바뀌거나 새로 들어오면 모든 데이터를 새로 받는다
TAG = f"kis-{KIS_KEY[-4:]}-{int(KIS_DEMO)}" if KIS else "yahoo"


@st.cache_data(ttl=300, show_spinner=False)
def check_kis(tag):
    """연결 점검: 토큰 발급 → 삼성전자 현재가 조회까지 실제로 해 본다."""
    if KIS is None:
        if HAS_KEYS:
            return False, "스위치가 꺼져 있습니다."
        return False, "Secrets에서 KIS_APP_KEY / KIS_APP_SECRET을 찾지 못했습니다."
    try:
        KIS.token()
    except Exception as e:
        return False, str(e)
    try:
        q = KIS.domestic_price("005930")
        return True, f"정상 (삼성전자 {q['price']:,.0f}원 조회 성공)"
    except Exception as e:
        return False, f"토큰은 성공, 시세 조회 실패 → {e}"


ANTHROPIC_KEY = secret("ANTHROPIC_API_KEY")
CLAUDE_MODEL = secret("CLAUDE_MODEL", "claude-sonnet-5-5")
UNIVERSE = build_universe()
# GitHub이 미리 계산해 둔 지표 파일 위치 (data 브랜치)
PRECOMPUTED_URL = str(secret("PRECOMPUTED_URL",
                             "https://raw.githubusercontent.com/sjkim222/stock-dashboard/data/"))
PRECOMPUTED_MAX_AGE = 36 * 3600  # 이보다 오래된 파일이면 사이트가 직접 계산
COUNTRY_KR = {"KR": "🇰🇷 한국", "US": "🇺🇸 미국"}


# ---------------- 캐시된 데이터 ----------------
METRICS_TTL = 3600  # 지표를 새로 계산하는 주기(초)


@st.cache_resource
def metrics_store(tag):
    """서버 메모리에 지표 표를 보관. 만료돼도 옛 표를 바로 보여주고 뒤에서 새로 계산한다."""
    return {"df": None, "fx": None, "ts": 0.0, "running": False, "error": None,
            "lock": threading.Lock()}


def _rebuild(store, kis):
    try:
        df, fx = market.build_metrics(UNIVERSE, kis)
        store.update(df=df, fx=fx, ts=time.time(), error=None)
    except Exception as e:
        store["error"] = str(e)
    finally:
        store["running"] = False


def start_rebuild(store):
    """이미 계산 중이 아니면 백그라운드 계산을 시작한다."""
    with store["lock"]:
        if store["running"]:
            return
        store["running"] = True
    threading.Thread(target=_rebuild, args=(store, KIS), daemon=True).start()


@st.cache_data(ttl=600, show_spinner=False)
def load_precomputed(url):
    """미리 계산된 지표 파일. 없거나 읽지 못하면 None."""
    try:
        meta = requests.get(url + "meta.json", timeout=10).json()
        res = requests.get(url + "metrics.csv", timeout=15)
        res.raise_for_status()
        df = pd.read_csv(io.StringIO(res.text), dtype={"code": str, "market": str})
        if df.empty:
            return None
        return df, float(meta["fx"]), float(meta["ts"]), meta.get("source", "?")
    except Exception:
        return None


def precomputed_fresh():
    pre = load_precomputed(PRECOMPUTED_URL)
    return pre if pre and time.time() - pre[2] < PRECOMPUTED_MAX_AGE else None


def load_metrics(tag):
    """반환: (지표 표, 환율, 기준 시각, 출처 설명)"""
    pre = precomputed_fresh()
    if pre:
        df, fx, ts, src = pre
        return df, fx, ts, f"미리 계산 ({src})"
    store = metrics_store(tag)
    if store["df"] is None:
        start_rebuild(store)
        with st.spinner("종목 지표를 처음 모으는 중입니다… (이후에는 바로 열립니다)"):
            while store["running"]:
                time.sleep(0.5)
        if store["df"] is None:
            st.error(f"지표를 불러오지 못했습니다: {store['error']}")
            st.stop()
    elif time.time() - store["ts"] > METRICS_TTL:
        start_rebuild(store)  # 옛 표를 보여주는 동안 뒤에서 갱신
    return store["df"], store["fx"], store["ts"], "사이트에서 직접 계산"


@st.cache_data(ttl=60, show_spinner="현재가 갱신 중…")
def load_live(df, tag):
    return market.refresh_prices(df, KIS)


@st.cache_data(ttl=10, show_spinner=False)
def load_indices(tag):
    return market.index_quotes(KIS)


@st.cache_data(ttl=300, show_spinner=False)
def load_stock_history(country, code, market_code, period, tag=None):
    row = {"country": country, "code": code, "market": market_code}
    return market.stock_history(row, period, KIS)


@st.cache_data(ttl=300, show_spinner=False)
def load_index_history(name, period, tag=None):
    it = next(i for c in INDICES.values() for i in c if i["name"] == name)
    return market.index_history(it, period, KIS)


# ---------------- 표시 도우미 ----------------
def fmt_price(v, cur):
    if v is None or pd.isna(v):
        return "-"
    return f"₩{v:,.0f}" if cur == "KRW" else f"${v:,.2f}"


def price_chart(h, title, candles=True):
    if h.empty:
        st.info("차트 데이터를 받지 못했습니다.")
        return
    fig = go.Figure()
    if candles:
        fig.add_trace(go.Candlestick(x=h.index, open=h["Open"], high=h["High"], low=h["Low"],
                                     close=h["Close"], name="가격",
                                     increasing_line_color="#d6334a", decreasing_line_color="#2f6fd6"))
        for n, color in ((20, "#e8a33d"), (60, "#7a5cc7")):
            if len(h) > n:
                fig.add_trace(go.Scatter(x=h.index, y=h["Close"].rolling(n).mean(),
                                         name=f"{n}일선", line=dict(width=1.3, color=color)))
    else:
        fig.add_trace(go.Scatter(x=h.index, y=h["Close"], name=title, line=dict(width=2)))
    fig.update_layout(title=title, height=420, margin=dict(l=10, r=10, t=40, b=10),
                      xaxis_rangeslider_visible=False, legend=dict(orientation="h", y=1.08))
    st.plotly_chart(fig, width="stretch")


RESULT_COLUMNS = {
    "country": st.column_config.TextColumn("국가", width="small"),
    "name": st.column_config.TextColumn("종목"),
    "code": st.column_config.TextColumn("코드", width="small"),
    "themes": st.column_config.TextColumn("테마"),
    "price_txt": st.column_config.TextColumn("현재가"),
    "change_pct": st.column_config.NumberColumn("등락률", format="%.2f%%"),
    "market_cap_jo": st.column_config.NumberColumn("시총(조원)", format="%.1f"),
    "per": st.column_config.NumberColumn("PER", format="%.1f"),
    "pbr": st.column_config.NumberColumn("PBR", format="%.2f"),
    "div_yield": st.column_config.NumberColumn("배당률", format="%.2f%%"),
    "ret_1m": st.column_config.NumberColumn("1개월", format="%.1f%%"),
    "ret_1y": st.column_config.NumberColumn("1년", format="%.1f%%"),
    "from_high_pct": st.column_config.NumberColumn("52주고점대비", format="%.1f%%"),
    "source": st.column_config.TextColumn("시세", width="small"),
}


def show_results(res: pd.DataFrame, key: str):
    if res.empty:
        st.warning("조건에 맞는 종목이 없습니다. 조건을 조금 완화해 보세요.")
        return
    view = res.copy()
    view["country"] = view["country"].map({"KR": "🇰🇷", "US": "🇺🇸"})
    view["price_txt"] = [fmt_price(p, c) for p, c in zip(res["price"], res["currency"])]
    st.caption(f"{len(view)}개 종목 · 행을 클릭하면 아래에 차트가 열립니다")
    event = st.dataframe(view[list(RESULT_COLUMNS)], column_config=RESULT_COLUMNS,
                         hide_index=True, width="stretch",
                         on_select="rerun", selection_mode="single-row", key=key)
    picked = event.selection.rows if event and event.selection else []
    if picked:
        row = res.iloc[picked[0]]
        st.divider()
        stock_detail(row.to_dict())


def stock_detail(row):
    q = None
    try:
        q = market.live_quote(row, KIS)
    except Exception as e:
        st.warning(f"현재가 조회 실패: {e}")
    cur = "KRW" if row["country"] == "KR" else "USD"
    c1, c2, c3, c4 = st.columns(4)
    if q:
        c1.metric(f'{row["name"]} ({row["code"]})', fmt_price(q["price"], cur),
                  f'{q["change_pct"]:+.2f}%' if q.get("change_pct") is not None else None,
                  delta_color="inverse" if cur == "KRW" else "normal")
        c2.metric("시세 기준", q.get("source", "-"))
    if row.get("per") is not None and not pd.isna(row.get("per")):
        c3.metric("PER", f'{row["per"]:.1f}')
    if row.get("market_cap_jo") is not None and not pd.isna(row.get("market_cap_jo")):
        c4.metric("시가총액", f'{row["market_cap_jo"]:,.1f}조원')
    period = st.radio("기간", ["1mo", "3mo", "6mo", "1y", "5y"], index=3, horizontal=True,
                      key=f'period_{row["code"]}',
                      format_func=lambda p: {"1mo": "1개월", "3mo": "3개월", "6mo": "6개월",
                                             "1y": "1년", "5y": "5년"}[p])
    h = load_stock_history(row["country"], row["code"], row.get("market", "KS"), period, tag=TAG)
    price_chart(h, row["name"], candles=True)


# ---------------- 사이드바 ----------------
with st.sidebar:
    ok, msg = check_kis(TAG)
    if ok:
        st.write("주 데이터: 🟢 한국투자증권 API (실시간)")
    elif HAS_KEYS and not USE_KIS:
        st.write("주 데이터: ⚪ 증권사 API 꺼짐 → 야후 (지연 시세)")
    elif KIS:
        st.write("주 데이터: 🔴 한국투자증권 연결 실패 → 야후로 대체 중")
    else:
        st.write("주 데이터: 🟡 야후 (지연 시세)")
    with st.expander("🔧 증권사 연결 점검", expanded=not ok and KIS is not None):
        st.write(f"APP KEY: {'✅ ' + str(len(KIS_KEY)) + '자 감지' if KIS_KEY else '❌ 없음'}")
        st.write(f"APP SECRET: {'✅ ' + str(len(KIS_SECRET)) + '자 감지' if KIS_SECRET else '❌ 없음'}")
        st.write(f"모드: {'모의투자' if KIS_DEMO else '실전'}")
        st.write(f"결과: {msg}")
        if st.button("다시 점검"):
            check_kis.clear()
            st.rerun()
    if KIS:
        st.caption("배당률은 증권사 시세 API에 없어 야후에서 보충합니다. 받지 못하면 빈칸입니다.")
    st.write("자연어 해석:", "🟢 Claude API" if ANTHROPIC_KEY else "🟡 규칙 기반")
    auto = st.toggle("지수 자동 새로고침 (10초)", value=bool(KIS))
    if st.button("🔄 전체 데이터 새로 받기"):
        st.cache_data.clear()
        start_rebuild(metrics_store(TAG))
        st.rerun()
    st.caption("키 설정 방법은 README.md 참고. 국내 시세 색상은 한국식(상승 빨강)입니다.")

st.title("📈 글로벌 주식 대시보드")
PAGES = ["🌏 대표지수", "🔍 종목 찾기", "📊 종목 상세"]
page = st.segmented_control("화면", PAGES, default=PAGES[0], key="page", label_visibility="collapsed") or PAGES[0]

# 첫 화면을 보는 동안 종목 지표를 미리 모아 둔다
if not precomputed_fresh() and metrics_store(TAG)["df"] is None:
    start_rebuild(metrics_store(TAG))


# ---------------- 탭 1: 대표지수 ----------------
if page == PAGES[0]:

    @st.fragment(run_every=10 if auto else None)
    def index_board():
        data = load_indices(TAG)
        for country in ("KR", "US"):
            st.subheader(COUNTRY_KR[country])
            cols = st.columns(len(data[country]))
            for col, it in zip(cols, data[country]):
                price = it.get("price")
                chg = it.get("change_pct")
                col.metric(
                    it["name"],
                    f"{price:,.2f}" if price else "-",
                    f"{chg:+.2f}%" if chg is not None else None,
                    delta_color="inverse" if country == "KR" else "normal",
                    help=f'출처: {it.get("source", "-")}',
                )
        st.caption(f'갱신: {pd.Timestamp.now(tz="Asia/Seoul"):%Y-%m-%d %H:%M:%S} (KST)')

    index_board()
    st.divider()
    all_idx = [it for c in ("KR", "US") for it in INDICES[c]]
    c1, c2 = st.columns([2, 3])
    pick = c1.selectbox("차트로 볼 지수", all_idx, format_func=lambda it: it["name"])
    per = c2.radio("기간 ", ["1mo", "6mo", "1y", "5y"], index=2, horizontal=True,
                   format_func=lambda p: {"1mo": "1개월", "6mo": "6개월", "1y": "1년", "5y": "5년"}[p])
    price_chart(load_index_history(pick["name"], per, tag=TAG), pick["name"], candles=False)

# ---------------- 탭 2: 종목 찾기 ----------------
if page == PAGES[1]:
    base, fx, ts, metric_src = load_metrics(TAG)
    # 지표를 막 계산했으면 현재가도 최신이므로 재조회 생략
    data = load_live(base, TAG) if KIS and time.time() - ts > 60 else base
    st.caption(f"검색 대상: {len(data)}개 종목 · 원/달러 {fx:,.0f}원 기준 시총 환산 · "
               f"지표 기준 {pd.Timestamp(ts, unit='s', tz='UTC').tz_convert('Asia/Seoul'):%m/%d %H:%M} ({metric_src})"
               + (" (갱신 중)" if metrics_store(TAG)["running"] else "") + (" · 현재가는 1분마다 실시간 갱신" if KIS else ""))

    mode = st.radio("검색 방식", ["💬 문장으로", "🏷️ 섹터·테마", "🔢 수치 조건"], horizontal=True)

    if mode == "💬 문장으로":
        examples = ["PER 15 이하인 미국 반도체주", "배당 3% 이상 한국 금융주", "52주 신고가 근처 방산주",
                    "고점 대비 많이 빠진 2차전지", "시총 100조 이상 AI 빅테크 1년 수익률 높은 순"]
        ex = st.pills("예시", examples, key="ex")
        q = st.text_input("원하는 종목을 문장으로 적어주세요", value=ex or "",
                          placeholder="예: 저평가된 한국 반도체 장비주")
        if q:
            with st.spinner("조건 해석 중…"):
                f, how, note = nl_parser.parse(q, ANTHROPIC_KEY, CLAUDE_MODEL)
            st.info(f"**해석 ({how})**: {describe(f)}" + (f"\n\n{note}" if note else ""))
            show_results(apply_filters(data, f), "res_nl")

    elif mode == "🏷️ 섹터·테마":
        c1, c2 = st.columns([1, 3])
        countries = c1.multiselect("국가", ["KR", "US"], default=["KR", "US"], format_func=COUNTRY_KR.get)
        themes = c2.multiselect("테마 (여러 개 선택 가능)", list(THEMES), default=["반도체"])
        sort_by = st.selectbox("정렬", ["market_cap_jo", "change_pct", "ret_1m", "ret_1y", "per", "div_yield"],
                               format_func=lambda k: {"market_cap_jo": "시가총액 큰 순", "change_pct": "오늘 등락률",
                                                      "ret_1m": "1개월 수익률", "ret_1y": "1년 수익률",
                                                      "per": "PER 낮은 순", "div_yield": "배당률 높은 순"}[k])
        f = {"countries": countries, "themes": themes, "sort_by": sort_by, "ascending": sort_by == "per"}
        show_results(apply_filters(data, f), "res_theme")

    else:
        c1, c2 = st.columns(2)
        countries = c1.multiselect("국가 ", ["KR", "US"], default=["KR", "US"], format_func=COUNTRY_KR.get)
        themes = c2.multiselect("테마 (비우면 전체)", list(THEMES))
        c1, c2, c3 = st.columns(3)
        mcap = c1.slider("시가총액 (조원)", 0, 6000, (0, 6000), step=10)
        per_rng = c2.slider("PER", 0, 100, (0, 100))
        div_min = c3.slider("배당률 최소 (%)", 0.0, 8.0, 0.0, 0.5)
        c1, c2, c3 = st.columns(3)
        r1y = c1.slider("1년 수익률 (%)", -100, 500, (-100, 500), step=5)
        r1m = c2.slider("1개월 수익률 (%)", -50, 100, (-50, 100))
        high = c3.slider("52주 고점 대비 이내 (%)", 0, 100, 100,
                         help="5로 두면 고점에서 5% 이내(신고가 근처)만 표시")
        f = {"countries": countries, "themes": themes}
        if mcap != (0, 6000):
            f["market_cap_min"], f["market_cap_max"] = mcap
        if per_rng != (0, 100):
            f["per_min"], f["per_max"] = per_rng
        if div_min > 0:
            f["div_min"] = div_min
        if r1y != (-100, 500):
            f["ret_1y_min"], f["ret_1y_max"] = r1y
        if r1m != (-50, 100):
            f["ret_1m_min"], f["ret_1m_max"] = r1m
        if high < 100:
            f["near_high_pct"] = high
        st.caption("적용 조건: " + describe(f))
        show_results(apply_filters(data, f), "res_num")

# ---------------- 탭 3: 종목 상세 ----------------
if page == PAGES[2]:
    c1, c2 = st.columns([3, 2])
    choice = c1.selectbox("등록된 종목에서 선택", UNIVERSE,
                          format_func=lambda r: f'{"🇰🇷" if r["country"] == "KR" else "🇺🇸"} {r["name"]} ({r["code"]})')
    free = c2.text_input("또는 직접 입력 (예: 005930, AAPL)", placeholder="종목코드/티커").strip().upper()
    if free:
        if free.isdigit() and len(free) == 6:
            row = {"country": "KR", "code": free, "name": free, "market": "KS"}
            # 코스피에 없으면 코스닥으로
            if not KIS and load_stock_history("KR", free, "KS", "5d", tag=TAG).empty:
                row["market"] = "KQ"
        else:
            row = {"country": "US", "code": free, "name": free, "market": "NAS"}
    else:
        row = dict(choice)
    stock_detail(row)
