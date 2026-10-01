"""
자연어 문장 → FILTER 변환.

1순위: Claude API (secrets에 ANTHROPIC_API_KEY가 있을 때) — 어떤 표현이든 유연하게 해석
2순위: 규칙 기반 파서 (키 없이 동작) — 자주 쓰는 표현만 인식
"""
import json
import re

from themes import THEMES

# ---------------- 규칙 기반 ----------------
_CMP_GE = ("이상", "넘", "초과", "보다 큰", "보다 높", "위")
_CMP_LE = ("이하", "미만", "아래", "보다 작", "보다 낮", "안쪽", "이내")


def _cmp(word):
    if any(w in word for w in _CMP_LE):
        return "le"
    if any(w in word for w in _CMP_GE):
        return "ge"
    return None


def _num_cond(text, label_pattern, unit_pattern=r"\s*"):
    """'PER 15 이하' 같은 표현에서 (값, 'ge'/'le') 추출."""
    m = re.search(label_pattern + r"\s*(?:이|가|은|는)?\s*(-?\d+(?:\.\d+)?)" + unit_pattern +
                  r"\s*(이상|이하|미만|초과|넘[는은게]?|아래|이내|보다\s*(?:큰|높은|작은|낮은))", text)
    if not m:
        return None
    return float(m.group(1)), _cmp(m.group(2))


def rule_parse(text: str) -> dict:
    t = text.lower().replace(",", "")
    f = {}

    # 국가
    countries = []
    if re.search(r"한국|국내|코스피|코스닥|kospi|kosdaq", t):
        countries.append("KR")
    if re.search(r"미국|해외|나스닥|뉴욕|s&p|nasdaq|us\b", t):
        countries.append("US")
    if countries:
        f["countries"] = countries

    # 테마
    themes = [name for name, spec in THEMES.items()
              if name.lower() in t or any(k.lower() in t for k in spec["keywords"])]
    # '반도체 장비'가 잡히면 일반 '반도체'는 장비 의도일 때 빼준다
    if "반도체 장비" in themes and "장비" in t and "반도체" in themes:
        themes.remove("반도체")
    if themes:
        f["themes"] = themes

    # 시가총액
    c = _num_cond(t, r"(?:시총|시가총액)", r"\s*조\s*원?")
    if c:
        f["market_cap_min" if c[1] == "ge" else "market_cap_max"] = c[0]
    if "대형주" in t:
        f.setdefault("market_cap_min", 10)
    if re.search(r"중소형|소형주", t):
        f.setdefault("market_cap_max", 5)

    # PER / PBR
    c = _num_cond(t, r"per")
    if c:
        f["per_max" if c[1] == "le" else "per_min"] = c[0]
    elif re.search(r"저평가|저per|싼|싸게", t):
        f["per_max"] = 15
    c = _num_cond(t, r"pbr")
    if c and c[1] == "le":
        f["pbr_max"] = c[0]
    elif re.search(r"저pbr|자산가치", t):
        f["pbr_max"] = 1

    # 배당
    c = _num_cond(t, r"배당(?:수익률|률)?", r"\s*%?")
    if c and c[1] == "ge":
        f["div_min"] = c[0]
    elif "고배당" in t or "배당주" in t:
        f["div_min"] = 3

    # 수익률
    c = _num_cond(t, r"(?:1년|일년|연간)\s*(?:수익률|상승률)?", r"\s*%")
    if c:
        f["ret_1y_min" if c[1] == "ge" else "ret_1y_max"] = c[0]
    c = _num_cond(t, r"(?:1개월|한\s*달|한달)\s*(?:수익률|상승률)?", r"\s*%")
    if c:
        f["ret_1m_min" if c[1] == "ge" else "ret_1m_max"] = c[0]

    # 52주 고점 관련
    if re.search(r"신고가|고점\s*근처|고점\s*부근", t):
        f["near_high_pct"] = 5
    if re.search(r"많이\s*(빠진|떨어진|하락한)|낙폭|폭락|조정\s*받은", t):
        f["far_from_high_pct"] = 20

    # 정렬
    if re.search(r"배당.*(높은|많은)\s*순", t):
        f["sort_by"], f["ascending"] = "div_yield", False
    elif re.search(r"(많이\s*오른|상승률\s*높은|수익률\s*높은)", t):
        f["sort_by"], f["ascending"] = "ret_1y", False
    elif re.search(r"per.*(낮은|싼)\s*순|싼\s*순", t):
        f["sort_by"], f["ascending"] = "per", True
    elif re.search(r"(오늘|당일).*(오른|상승)", t):
        f["sort_by"], f["ascending"] = "change_pct", False

    # 개수
    m = re.search(r"(?:상위|top)\s*(\d+)|(\d+)\s*(?:개|종목)", t)
    if m:
        f["limit"] = int(m.group(1) or m.group(2))
    return f


# ---------------- Claude API ----------------
_FILTER_TOOL = {
    "name": "set_filters",
    "description": "사용자의 종목 검색 문장을 스크리너 필터로 변환한다. 언급되지 않은 조건은 넣지 않는다.",
    "input_schema": {
        "type": "object",
        "properties": {
            "countries": {"type": "array", "items": {"type": "string", "enum": ["KR", "US"]}},
            "themes": {"type": "array", "items": {"type": "string", "enum": list(THEMES.keys())}},
            "market_cap_min": {"type": "number", "description": "시가총액 하한, 조 원(원화 환산)"},
            "market_cap_max": {"type": "number", "description": "시가총액 상한, 조 원"},
            "per_min": {"type": "number"},
            "per_max": {"type": "number"},
            "pbr_max": {"type": "number"},
            "div_min": {"type": "number", "description": "배당수익률 하한 %"},
            "ret_1m_min": {"type": "number"}, "ret_1m_max": {"type": "number"},
            "ret_1y_min": {"type": "number"}, "ret_1y_max": {"type": "number"},
            "near_high_pct": {"type": "number", "description": "52주 고점 대비 이 % 이내"},
            "far_from_high_pct": {"type": "number", "description": "52주 고점 대비 이 % 이상 하락"},
            "sort_by": {"type": "string", "enum": ["market_cap_jo", "per", "fwd_per", "pbr", "div_yield",
                                                    "ret_1m", "ret_3m", "ret_1y", "change_pct", "from_high_pct"]},
            "ascending": {"type": "boolean"},
            "limit": {"type": "integer"},
            "explanation": {"type": "string", "description": "어떻게 해석했는지 한국어 한두 문장"},
        },
    },
}

_SYSTEM = (
    "너는 주식 스크리너의 조건 해석기다. 사용자의 문장을 set_filters 도구 입력으로 바꿔라. "
    "모호한 표현은 일반적인 기준으로 수치화한다: 저평가→PER 15 이하, 고배당→배당 3% 이상, "
    "대형주→시총 10조 이상, 중소형주→시총 5조 이하, 신고가 근처→52주 고점 5% 이내, "
    "많이 빠진→고점 대비 20% 이상 하락. 테마는 주어진 목록에서 가장 가까운 것을 고르고, "
    "목록에 없는 테마면 themes를 비우고 explanation에 그 사실을 적는다."
)


def claude_parse(text: str, api_key: str, model: str) -> dict:
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model=model,
        max_tokens=800,
        system=_SYSTEM,
        tools=[_FILTER_TOOL],
        tool_choice={"type": "tool", "name": "set_filters"},
        messages=[{"role": "user", "content": text}],
    )
    for block in msg.content:
        if block.type == "tool_use":
            return dict(block.input)
    raise RuntimeError("필터를 해석하지 못했습니다: " + json.dumps([b.type for b in msg.content]))


def parse(text: str, api_key: str = None, model: str = "claude-sonnet-5-5"):
    """(filters, 사용한 방식, 해석 설명) 반환."""
    if api_key:
        try:
            f = claude_parse(text, api_key, model)
            return f, "Claude", f.pop("explanation", "")
        except Exception as e:
            f = rule_parse(text)
            return f, "규칙", f"Claude 호출 실패로 규칙 파서 사용 ({e})"
    return rule_parse(text), "규칙", ""
