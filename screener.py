"""
조건 필터. 섹터·테마 선택, 수치 조건 입력, 자연어 문장 모두 결국 이 FILTER 형식으로 바뀐다.

FILTER 키 (모두 선택 사항, 없으면 조건 없음)
  countries / exclude_countries   ["KR", "US"]
  themes / exclude_themes         ["반도체", ...]
  codes / exclude_codes           특정 종목만 / 특정 종목 제외
  market_cap_min / max            시가총액 (조 원, 원화 환산)
  per_min / per_max               PER (per_max가 있으면 적자·PER 없음 제외)
  pbr_min / pbr_max               PBR
  div_min                         배당수익률 하한 (%)
  change_pct_min / max            오늘 등락률 (%)
  ret_1m_ / ret_3m_ / ret_1y_ min·max   기간 수익률 (%)
  div_cagr_5y_min / max           5년 연평균 배당 성장률 (%)
  div_up_years_min                배당 연속 증가 연수
  near_high_pct                   52주 고점 대비 이 % 이내
  far_from_high_pct               52주 고점 대비 이 % 이상 하락
  sort_by / ascending / limit     정렬·개수
"""
import pandas as pd

SORTABLE = {"market_cap_jo", "per", "fwd_per", "pbr", "div_yield", "ret_1m", "ret_3m",
            "ret_1y", "change_pct", "from_high_pct", "div_cagr_5y", "div_up_years"}
RANGES = [  # (컬럼, 하한 키, 상한 키)
    ("market_cap_jo", "market_cap_min", "market_cap_max"),
    ("per", "per_min", "per_max"),
    ("pbr", "pbr_min", "pbr_max"),
    ("div_yield", "div_min", "div_max"),
    ("change_pct", "change_pct_min", "change_pct_max"),
    ("ret_1m", "ret_1m_min", "ret_1m_max"),
    ("ret_3m", "ret_3m_min", "ret_3m_max"),
    ("ret_1y", "ret_1y_min", "ret_1y_max"),
    ("div_cagr_5y", "div_cagr_5y_min", "div_cagr_5y_max"),
    ("div_up_years", "div_up_years_min", "div_up_years_max"),
]


def _theme_set(s):
    return set(x.strip() for x in str(s).split(",")) if isinstance(s, str) else set()


def apply_filters(df: pd.DataFrame, f: dict) -> pd.DataFrame:
    if df.empty:
        return df
    m = pd.Series(True, index=df.index)

    if f.get("countries"):
        m &= df["country"].isin(f["countries"])
    if f.get("exclude_countries"):
        m &= ~df["country"].isin(f["exclude_countries"])
    if f.get("themes"):
        wanted = set(f["themes"])
        m &= df["themes"].apply(lambda s: bool(wanted & _theme_set(s)))
    if f.get("exclude_themes"):
        unwanted = set(f["exclude_themes"])
        m &= ~df["themes"].apply(lambda s: bool(unwanted & _theme_set(s)))
    if f.get("codes"):
        m &= df["code"].astype(str).isin([str(c) for c in f["codes"]])
    if f.get("exclude_codes"):
        m &= ~df["code"].astype(str).isin([str(c) for c in f["exclude_codes"]])

    missing = []  # 데이터에 컬럼이 없어 적용하지 못한 조건
    for col, lo_key, hi_key in RANGES:
        lo, hi = f.get(lo_key), f.get(hi_key)
        if col not in df:
            if lo is not None or hi is not None:
                missing.append(col)
            continue
        if lo is not None:
            m &= df[col].notna() & (df[col] >= lo)
        if hi is not None:
            m &= df[col].notna() & (df[col] <= hi)
    if f.get("per_max") is not None:
        m &= df["per"] > 0  # 적자(음수 PER) 제외

    if f.get("near_high_pct") is not None:
        m &= df["from_high_pct"].notna() & (df["from_high_pct"] >= -abs(f["near_high_pct"]))
    if f.get("far_from_high_pct") is not None:
        m &= df["from_high_pct"].notna() & (df["from_high_pct"] <= -abs(f["far_from_high_pct"]))

    out = df[m].copy()
    sort_by = f.get("sort_by") if f.get("sort_by") in SORTABLE else "market_cap_jo"
    if sort_by not in out.columns:
        missing.append(sort_by)
        sort_by = "market_cap_jo"
    out = out.sort_values(sort_by, ascending=bool(f.get("ascending", False)), na_position="last")
    if f.get("limit"):
        out = out.head(int(f["limit"]))
    out.attrs["missing"] = [SORT_NAMES.get(c, c) for c in dict.fromkeys(missing)]
    return out


SORT_NAMES = {"market_cap_jo": "시가총액", "per": "PER", "fwd_per": "예상 PER", "pbr": "PBR",
              "div_yield": "배당률", "change_pct": "오늘 등락률", "ret_1m": "1개월 수익률",
              "ret_3m": "3개월 수익률", "ret_1y": "1년 수익률", "from_high_pct": "52주 고점 대비",
              "div_cagr_5y": "5년 배당성장률", "div_up_years": "연속 배당 증가"}


def _fmt(v):
    return f"{v:g}"


def describe(f: dict, names: dict = None) -> str:
    """적용된 조건을 사람이 읽을 수 있는 한 줄로. names: 종목코드→이름"""
    names = names or {}
    country = {"KR": "한국", "US": "미국"}
    parts = []
    if f.get("countries"):
        parts.append("/".join(country.get(c, c) for c in f["countries"]))
    if f.get("exclude_countries"):
        parts.append("제외: " + "/".join(country.get(c, c) for c in f["exclude_countries"]))
    if f.get("themes"):
        parts.append("테마: " + ", ".join(f["themes"]))
    if f.get("exclude_themes"):
        parts.append("테마 제외: " + ", ".join(f["exclude_themes"]))
    if f.get("codes"):
        parts.append("종목: " + ", ".join(names.get(c, c) for c in f["codes"]))
    if f.get("exclude_codes"):
        parts.append("종목 제외: " + ", ".join(names.get(c, c) for c in f["exclude_codes"]))

    labels = {
        "market_cap_jo": ("시총", "조"), "per": ("PER", ""), "pbr": ("PBR", ""), "div_yield": ("배당", "%"),
        "change_pct": ("오늘", "%"), "ret_1m": ("1개월", "%"), "ret_3m": ("3개월", "%"), "ret_1y": ("1년", "%"),
        "div_cagr_5y": ("5년 배당성장률", "%"), "div_up_years": ("배당 연속 증가", "년"),
    }
    for col, lo_key, hi_key in RANGES:
        name, unit = labels[col]
        lo, hi = f.get(lo_key), f.get(hi_key)
        if col == "per" and lo is not None and lo <= 0.01 and hi is None:
            parts.append("흑자(PER>0)")
            continue
        if col == "per" and lo is not None and lo <= 0.01:
            lo = None
        if lo is not None and hi is not None:
            parts.append(f"{name} {_fmt(lo)}~{_fmt(hi)}{unit}")
        elif lo is not None:
            parts.append(f"{name} ≥ {_fmt(lo)}{unit}")
        elif hi is not None:
            parts.append(f"{name} ≤ {_fmt(hi)}{unit}")
    if f.get("near_high_pct") is not None:
        parts.append(f"52주 고점 -{_fmt(f['near_high_pct'])}% 이내")
    if f.get("far_from_high_pct") is not None:
        parts.append(f"52주 고점 대비 -{_fmt(f['far_from_high_pct'])}% 이상 하락")
    if f.get("sort_by"):
        parts.append(f'정렬: {SORT_NAMES.get(f["sort_by"], f["sort_by"])} {"낮은 순" if f.get("ascending") else "높은 순"}')
    if f.get("limit"):
        parts.append(f'{f["limit"]}개')
    return " · ".join(parts) if parts else "조건 없음 (전체)"
