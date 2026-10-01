"""
나라별 대표지수 + 조건별 종목 찾기 대시보드

실행:  streamlit run app.py
"""
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import market
import nl_parser
from kis import KISClient
from screener import apply_filters, describe
from themes import INDICES, THEMES, build_universe, yf_symbol

st.set_page_config(page_title="글로벌 주식 대시보드", page_icon="📈", layout="wide")


# ---------------- 설정 ----------------
def secret(key, default=None):
    try:
        return st.secrets.get(key, default)
    except Exception:  # secrets.toml이 없을 때
        return default


@st.cache_resource
def get_kis():
    key, sec = secret("KIS_APP_KEY"), secret("KIS_APP_SECRET")
    if not key or not sec:
        return None
    return KISClient(key, sec, demo=str(secret("KIS_DEMO", "false")).lower() == "true")


KIS = get_kis()
ANTHROPIC_KEY = secret("ANTHROPIC_API_KEY")
CLAUDE_MODEL = secret("CLAUDE_MODEL", "claude-sonnet-5-5")
UNIVERSE = build_universe()
COUNTRY_KR = {"KR": "🇰🇷 한국", "US": "🇺🇸 미국"}


# ---------------- 캐시된 데이터 ----------------
@st.cache_data(ttl=3600, show_spinner="종목 지표(수익률·PER·배당) 불러오는 중… 처음 한 번은 30초 정도 걸립니다")
def load_metrics():
    return market.build_metrics(UNIVERSE)


@st.cache_data(ttl=20, show_spinner=False)
def load_live(df):
    return market.overlay_live(df, KIS)


@st.cache_data(ttl=10, show_spinner=False)
def load_indices():
    return market.index_quotes(KIS)


@st.cache_data(ttl=600, show_spinner=False)
def load_history(symbol, period, interval):
    return market.history(symbol, period, interval)


# ---------------- 표시 도우미 ----------------
def fmt_price(v, cur):
    if v is None or pd.isna(v):
        return "-"
    return f"₩{v:,.0f}" if cur == "KRW" else f"${v:,.2f}"


def price_chart(symbol, title, period="1y", interval="1d", candles=True):
    h = load_history(symbol, period, interval)
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
    interval = "1wk" if period == "5y" else "1d"
    price_chart(yf_symbol(row), row["name"], period, interval, candles=True)


# ---------------- 사이드바 ----------------
with st.sidebar:
    st.header("⚙️ 연결 상태")
    st.write("실시간 시세:", "🟢 한국투자증권 API" if KIS else "🟡 미연결 (야후 지연 시세)")
    st.write("자연어 해석:", "🟢 Claude API" if ANTHROPIC_KEY else "🟡 규칙 기반")
    auto = st.toggle("지수 자동 새로고침 (10초)", value=bool(KIS))
    if st.button("🔄 전체 데이터 새로 받기"):
        st.cache_data.clear()
        st.rerun()
    st.caption("키 설정 방법은 README.md 참고. 국내 시세 색상은 한국식(상승 빨강)입니다.")

st.title("📈 글로벌 주식 대시보드")
tab_idx, tab_find, tab_one = st.tabs(["🌏 대표지수", "🔍 종목 찾기", "📊 종목 상세"])

# ---------------- 탭 1: 대표지수 ----------------
with tab_idx:

    @st.fragment(run_every=10 if auto else None)
    def index_board():
        data = load_indices()
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
    price_chart(pick["yf"], pick["name"], per, "1wk" if per == "5y" else "1d", candles=False)

# ---------------- 탭 2: 종목 찾기 ----------------
with tab_find:
    base, fx = load_metrics()
    data = load_live(base) if KIS else base
    st.caption(f"검색 대상: themes.py에 등록된 {len(data)}개 종목 · 원/달러 {fx:,.0f}원 기준으로 시총 환산")

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
with tab_one:
    c1, c2 = st.columns([3, 2])
    choice = c1.selectbox("등록된 종목에서 선택", UNIVERSE,
                          format_func=lambda r: f'{"🇰🇷" if r["country"] == "KR" else "🇺🇸"} {r["name"]} ({r["code"]})')
    free = c2.text_input("또는 직접 입력 (예: 005930, AAPL)", placeholder="종목코드/티커").strip().upper()
    if free:
        if free.isdigit() and len(free) == 6:
            row = {"country": "KR", "code": free, "name": free, "market": "KS"}
            # 코스피에 없으면 코스닥으로
            if load_history(f"{free}.KS", "5d", "1d").empty:
                row["market"] = "KQ"
        else:
            row = {"country": "US", "code": free, "name": free, "market": "NAS"}
    else:
        row = dict(choice)
    stock_detail(row)
