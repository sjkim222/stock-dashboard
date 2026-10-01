"""
조건 필터. 섹터·테마 선택, 수치 조건 입력, 자연어 문장 모두 결국 이 FILTER 형식으로 바뀐다.

FILTER 키 (모두 선택 사항, 없으면 조건 없음)
  countries        ["KR", "US"]
  themes           ["반도체", ...]
  market_cap_min   시가총액 하한 (조 원, 원화 환산)
  market_cap_max   시가총액 상한 (조 원)
  per_min/per_max  PER 범위 (적자 기업은 PER 없음 → per 조건이 있으면 제외)
  pbr_max          PBR 상한
  div_min          배당수익률 하한 (%)
  ret_1m_min/max   1개월 수익률 범위 (%)
  ret_1y_min/max   1년 수익률 범위 (%)
  near_high_pct    52주 고점 대비 이 % 이내 (예: 5 → 고점에서 -5% 안쪽)
  far_from_high_pct 52주 고점 대비 이 % 이상 하락 (낙폭 과대주)
  sort_by          정렬 기준 컬럼 (market_cap_jo, per, div_yield, ret_1m, ret_1y, change_pct ...)
  ascending        True면 오름차순
  limit            최대 표시 개수
"""
import pandas as pd

SORTABLE = {"market_cap_jo", "per", "fwd_per", "pbr", "div_yield", "ret_1m", "ret_3m",
            "ret_1y", "change_pct", "from_high_pct"}


def apply_filters(df: pd.DataFrame, f: dict) -> pd.DataFrame:
    if df.empty:
        return df
    m = pd.Series(True, index=df.index)

    if f.get("countries"):
        m &= df["country"].isin(f["countries"])
    if f.get("themes"):
        wanted = set(f["themes"])
        m &= df["themes"].apply(lambda s: bool(wanted & set(x.strip() for x in s.split(","))))

    def rng(col, lo_key, hi_key):
        nonlocal m
        lo, hi = f.get(lo_key), f.get(hi_key)
        if lo is not None:
            m &= df[col].notna() & (df[col] >= lo)
        if hi is not None:
            m &= df[col].notna() & (df[col] <= hi)

    rng("market_cap_jo", "market_cap_min", "market_cap_max")
    rng("per", "per_min", "per_max")
    rng("pbr", "pbr_min", "pbr_max")
    rng("div_yield", "div_min", "div_max")
    rng("ret_1m", "ret_1m_min", "ret_1m_max")
    rng("ret_1y", "ret_1y_min", "ret_1y_max")
    if f.get("per_max") is not None:
        m &= df["per"] > 0  # 적자(음수 PER) 제외

    if f.get("near_high_pct") is not None:
        m &= df["from_high_pct"].notna() & (df["from_high_pct"] >= -abs(f["near_high_pct"]))
    if f.get("far_from_high_pct") is not None:
        m &= df["from_high_pct"].notna() & (df["from_high_pct"] <= -abs(f["far_from_high_pct"]))

    out = df[m].copy()
    sort_by = f.get("sort_by") if f.get("sort_by") in SORTABLE else "market_cap_jo"
    out = out.sort_values(sort_by, ascending=bool(f.get("ascending", False)), na_position="last")
    if f.get("limit"):
        out = out.head(int(f["limit"]))
    return out


def describe(f: dict) -> str:
    """적용된 조건을 사람이 읽을 수 있는 한 줄로."""
    parts = []
    names = {"KR": "한국", "US": "미국"}
    if f.get("countries"):
        parts.append("/".join(names.get(c, c) for c in f["countries"]))
    if f.get("themes"):
        parts.append("테마: " + ", ".join(f["themes"]))
    labels = [
        ("market_cap_min", "시총 ≥ {}조"), ("market_cap_max", "시총 ≤ {}조"),
        ("per_min", "PER ≥ {}"), ("per_max", "PER ≤ {}"), ("pbr_max", "PBR ≤ {}"),
        ("div_min", "배당 ≥ {}%"), ("ret_1m_min", "1개월 ≥ {}%"), ("ret_1m_max", "1개월 ≤ {}%"),
        ("ret_1y_min", "1년 ≥ {}%"), ("ret_1y_max", "1년 ≤ {}%"),
        ("near_high_pct", "52주 고점 -{}% 이내"), ("far_from_high_pct", "52주 고점 대비 -{}% 이상 하락"),
    ]
    for k, tmpl in labels:
        if f.get(k) is not None:
            parts.append(tmpl.format(f[k]))
    if f.get("sort_by"):
        parts.append(f'정렬: {f["sort_by"]} {"↑" if f.get("ascending") else "↓"}')
    return " · ".join(parts) if parts else "조건 없음 (전체)"
