"""
AI 해설 — 검색 결과 표를 근거로 Claude가 사용자의 질문에 대화체로 답한다.

원칙
  - 표에 있는 숫자만 근거로 쓴다 (뉴스·실적 등 표에 없는 사실은 지어내지 않는다)
  - 없는 데이터를 요청받았으면 없다고 말한다
  - 매수·매도 지시가 아니라 판단 재료를 정리해 준다
"""
import pandas as pd

from nl_parser import AVAILABLE_DATA, MISSING_DATA

COLUMNS = {
    "name": "종목", "country": "국가", "themes": "테마", "price": "현재가", "change_pct": "오늘%",
    "market_cap_jo": "시총(조원)", "per": "PER", "pbr": "PBR", "div_yield": "배당률%",
    "div_cagr_5y": "5년배당성장%", "div_up_years": "배당연속증가(년)",
    "ret_1m": "1개월%", "ret_3m": "3개월%", "ret_1y": "1년%", "from_high_pct": "52주고점대비%",
}

SYSTEM = (
    "너는 개인 투자자의 주식 대시보드에 붙은 해설 도우미다. 사용자의 질문, 스크리너가 해석한 조건, "
    "조건에 맞는 종목 표가 주어진다. 한국어 대화체로 답하라.\n"
    "규칙:\n"
    "1. 질문에 대한 답을 첫 문장에 바로 말한다.\n"
    "2. 근거는 표의 숫자만 쓴다. 표에 없는 뉴스·실적·전망·사업 내용은 지어내지 않는다. "
    "종목의 사업을 설명해야 하면 일반적으로 널리 알려진 수준에서 한 구절만 쓴다.\n"
    f"3. 보유 데이터는 {AVAILABLE_DATA}뿐이다. {MISSING_DATA} 같은 데이터는 없다. "
    "질문이 없는 데이터를 요구하면 그 점을 짧게 밝히고, 표로 판단할 수 있는 범위에서 답한다.\n"
    "4. 눈에 띄는 종목 2~4개를 골라 왜 눈에 띄는지 숫자로 설명한다 (예: 배당 4.2%에 5년 배당성장 8%).\n"
    "5. 서로 상충하는 신호가 있으면 짚는다 (예: PER은 낮지만 1년 -40%로 시장이 뭔가를 우려 중일 수 있음).\n"
    "6. 사거나 팔라고 단정하지 않는다. 대신 추가로 확인해 볼 점을 1~2개 제안한다.\n"
    "7. 전체 6~10문장 분량. 필요하면 짧은 글머리표를 써도 된다. 마크다운 제목은 쓰지 않는다.\n"
    "8. 결과가 0개면 조건이 너무 좁다고 말하고 어떤 조건을 완화하면 좋을지 제안한다."
)


def table_for_prompt(df: pd.DataFrame, limit: int = 20) -> str:
    cols = [c for c in COLUMNS if c in df.columns]
    view = df[cols].head(limit).copy()
    for c in view.columns:
        if c not in ("name", "country", "themes"):
            view[c] = pd.to_numeric(view[c], errors="coerce").round(2)
    view = view.rename(columns=COLUMNS)
    return view.to_csv(index=False)


def build_messages(question: str, filter_desc: str, interp_note: str, df: pd.DataFrame, data_time: str):
    body = (
        f"[질문]\n{question}\n\n"
        f"[해석된 조건]\n{filter_desc}\n"
        + (f"[해석 메모]\n{interp_note}\n" if interp_note else "")
        + f"\n[데이터 기준 시각] {data_time}\n"
        f"[조건에 맞는 종목: 총 {len(df)}개, 아래는 정렬 순서 상위 {min(len(df), 20)}개]\n"
        f"{table_for_prompt(df) if len(df) else '(없음)'}"
    )
    return [{"role": "user", "content": body}]


def stream_answer(question, filter_desc, interp_note, df, data_time, api_key, model):
    """Claude 답변을 조각 단위로 내보내는 제너레이터 (st.write_stream 용)."""
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    with client.messages.stream(
        model=model,
        max_tokens=900,
        system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=build_messages(question, filter_desc, interp_note, df, data_time),
    ) as stream:
        for text in stream.text_stream:
            yield text
