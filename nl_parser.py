"""
자연어 문장 → FILTER 변환.

1순위: Claude API (secrets에 ANTHROPIC_API_KEY가 있을 때)
2순위: 규칙 기반 해석기 (키 없이 동작)

규칙 기반 해석기가 이해하는 것
  - 비교 표현: 이상/넘게/초과/최소, 이하/미만/까지/넘지 않는/최대, 10~20 사이
  - 단위: 조·천억·억 원, 달러, %, 배
  - 방향 있는 수익률: "1년간 50% 넘게 오른", "한 달 새 10% 이상 빠진", "오늘 3% 이상 급등"
  - 개념어: 저평가, 고배당, 우량주, 대형주, 모멘텀, 반등, 흑자, 신고가, 낙폭과대 …
  - 종목 이름·별칭: "엔비디아 같은", "삼전이랑 하닉 비교"
  - 제외: "반도체 빼고", "미국 말고", "테슬라 제외"
  - 오타: "반도채" → 반도체
  - 정렬·개수: "배당 높은 순", "많이 오른 순", "상위 5개", "세 종목"
"""
import difflib
import json
import re

from themes import THEMES, build_universe

UNIVERSE = build_universe()
_BY_CODE = {r["code"]: r for r in UNIVERSE}

# 자주 쓰는 종목 별칭 (소문자, 공백 없이)
ALIASES = {
    "삼전": "005930", "하이닉스": "000660", "하닉": "000660", "sk하닉": "000660",
    "현차": "005380", "현대자동차": "005380", "모비스": "012330", "엔솔": "373220", "lg엔솔": "373220",
    "삼바": "207940", "한화에어로": "012450", "에어로스페이스": "012450",
    "네이버": "035420", "naver": "035420", "포스코퓨처엠": "003670", "한전기술": None,
    "엔디비아": "NVDA", "엔비": "NVDA", "nvidia": "NVDA", "애플": "AAPL", "apple": "AAPL",
    "테슬라": "TSLA", "tesla": "TSLA", "마소": "MSFT", "마이크로소프트": "MSFT", "microsoft": "MSFT",
    "구글": "GOOGL", "google": "GOOGL", "알파벳": "GOOGL", "아마존": "AMZN", "amazon": "AMZN",
    "메타": "META", "페이스북": "META", "tsmc": "TSM", "브로드컴": "AVGO", "마이크론": "MU",
    "인텔": "INTC", "퀄컴": "QCOM", "팔란티어": None, "asml": "ASML", "램리서치": "LRCX",
    "일라이릴리": "LLY", "릴리": "LLY", "록히드": "LMT", "코카콜라": "KO", "코스트코": "COST",
}
_TICKER_STOP = {"AI", "EV", "US", "USA", "PER", "PBR", "ETF", "HBM", "GPU", "KR", "TOP", "SMR", "LNG", "CDMO", "M7"}

KOR_NUM = {"한": 1, "하나": 1, "두": 2, "둘": 2, "세": 3, "셋": 3, "네": 4, "넷": 4, "다섯": 5,
           "여섯": 6, "일곱": 7, "여덟": 8, "아홉": 9, "열": 10, "스무": 20}

NUM = r"(-?\d+(?:\.\d+)?)"
LE_WORDS = r"넘지\s*않|안\s*넘|이하|미만|아래|밑|까지|under|below|보다\s*(?:작|낮|적|싸)|이내|안쪽"
GE_WORDS = r"이상|넘|초과|over|above|보다\s*(?:크|큰|높|많|비싸)"
DOWN_WORDS = r"빠|하락|떨어|내린|내려|급락|폭락|손실|마이너스|깨진"
UP_WORDS = r"오른|올랐|올라|상승|급등|뛴|뛰|플러스|수익"
EXCLUDE_WORDS = r"빼고|빼|제외|말고|아닌|외에|외의"
SIMILAR_WORDS = r"같은|비슷한|유사|관련주|관련|경쟁사|경쟁|동종|피어|peer|대체"


# ======================= 도우미 =======================
def _norm(text: str) -> str:
    t = text.lower()
    t = t.replace("％", "%").replace("～", "~").replace("〜", "~")
    t = re.sub(r"(?<=\d),(?=\d{3})", "", t)  # 1,000 → 1000
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def _window_after(t, pos, n=14):
    """숫자 뒤 비교어를 찾는 창. 다음 숫자가 나오면 거기서 끊는다."""
    w = t[pos:pos + n]
    m = re.search(r"\d", w)
    return w[:m.start()] if m else w


def _cmp(t, num_start, num_end):
    after = _window_after(t, num_end)
    before = t[max(0, num_start - 6):num_start]
    if re.search(LE_WORDS, after) or re.search(r"최대|많아야|기껏", before):
        return "le"
    if re.search(GE_WORDS, after) or re.search(r"최소|적어도|최저", before):
        return "ge"
    return None


def _to_jo(value, unit, currency, fx):
    """시가총액 표현을 '조 원' 단위로."""
    unit = unit or "조"
    mult = {"조": 1.0, "천억": 0.1, "억": 1e-4, "b": 1e-3, "bn": 1e-3, "billion": 1e-3,
            "t": 1.0, "trillion": 1.0}.get(unit, 1.0)
    jo = value * mult
    if currency in ("달러", "$", "usd") or unit in ("b", "bn", "billion", "t", "trillion"):
        jo *= fx
    return round(jo, 2)


def _strip_josa(tok):
    return re.sub(r"(들|주|은|는|이|가|을|를|의|만|도|랑|하고|과|와|에서|으로|로)$", "", tok)


class _Ctx:
    def __init__(self, text, fx):
        self.raw = text
        self.t = _norm(text)
        self.fx = fx
        self.f = {}
        self.notes = []
        self.used = []  # 이미 해석한 숫자의 위치

    def used_at(self, a, b):
        return any(a < e and s < b for s, e in self.used)

    def set(self, key, value, force=False):
        if force or key not in self.f:
            self.f[key] = value


# ======================= 수치 조건 =======================
_METRICS = [
    # (키 접두어, 지표 표현, 숫자 뒤 단위)
    ("per", r"per|주가수익비율|피이알", r"\s*배?"),
    ("pbr", r"pbr|주가순자산비율|피비알", r"\s*배?"),
    ("div_cagr_5y", r"배당\s*(?:성장률|성장|증가율|증가)", r"\s*(?:%|퍼센트|프로)?"),
    ("div", r"배당(?:수익률|률|금)?(?!\s*(?:성장|증가))", r"\s*(?:%|퍼센트|프로)?"),
]


def _parse_range(c, key_min, key_max, metric_re, unit_re):
    m = re.search(rf"(?:{metric_re})[^\d\-]{{0,8}}?{NUM}{unit_re}\s*(?:~|-|에서|부터)\s*{NUM}{unit_re}",
                  c.t)
    if m:
        lo, hi = sorted([float(m.group(1)), float(m.group(2))])
        c.set(key_min, lo, True)
        c.set(key_max, hi, True)
        c.used.append(m.span())
        return True
    return False


def _parse_metric(c, prefix, metric_re, unit_re):
    if _parse_range(c, f"{prefix}_min", f"{prefix}_max", metric_re, unit_re):
        return
    for m in re.finditer(rf"(?:{metric_re})(?:이|가|은|는|이가)?[^\d\-가-힣]{{0,3}}[가-힣\s]{{0,6}}?{NUM}{unit_re}",
                         c.t):
        a, b = m.span(1)
        if c.used_at(a, b):
            continue
        v = float(m.group(1))
        d = _cmp(c.t, a, m.end())
        if d is None:
            d = "ge" if prefix in ("div", "div_cagr_5y") else "le"  # "배당 3%" → 3% 이상, "PER 15" → 15 이하
        c.set(f"{prefix}_{'min' if d == 'ge' else 'max'}", v, True)
        c.used.append((a, m.end()))


def _parse_mcap(c):
    unit = r"\s*(조|천억|억|bn|billion|trillion|b|t)?\s*(원|달러|\$|usd)?"
    metric = r"시총|시가총액|시가 총액|덩치|규모|몸집"
    m = re.search(rf"(?:{metric})[^\d]{{0,8}}?{NUM}{unit}\s*(?:~|-|에서|부터)\s*{NUM}{unit}", c.t)
    if m:
        lo = _to_jo(float(m.group(1)), m.group(2) or m.group(5), m.group(3) or m.group(6), c.fx)
        hi = _to_jo(float(m.group(4)), m.group(5) or m.group(2), m.group(6) or m.group(3), c.fx)
        c.set("market_cap_min", min(lo, hi), True)
        c.set("market_cap_max", max(lo, hi), True)
        c.used.append(m.span())
        return
    # "시총 10조 이상" / "10조 넘는 대형주" (지표 단어 없이 '조' 단위만 있어도 시총으로 본다)
    pat = rf"(?:(?:{metric})[^\d]{{0,8}}?)?{NUM}\s*(조|천억|억)\s*(원|달러|\$)?"
    for m in re.finditer(pat, c.t):
        a, b = m.span(1)
        if c.used_at(a, b):
            continue
        jo = _to_jo(float(m.group(1)), m.group(2), m.group(3), c.fx)
        d = _cmp(c.t, a, m.end()) or "ge"
        c.set("market_cap_min" if d == "ge" else "market_cap_max", jo, True)
        c.used.append((a, m.end()))


def _parse_div_streak(c):
    """'5년 연속 배당 증가', '10년 이상 배당 늘린'"""
    m = re.search(rf"{NUM}\s*년\s*(?:이상\s*)?(?:연속|째|동안|넘게)?\s*(?:으로\s*)?(?:배당)?\s*(?:을|를)?\s*(?:증가|늘|올린|올려|인상|성장)", c.t) \
        or re.search(rf"배당\s*(?:을|를)?\s*{NUM}\s*년\s*(?:이상\s*)?(?:연속|째|동안)?\s*(?:증가|늘|올린|올려|인상|성장)", c.t)
    if m:
        c.set("div_up_years_min", float(m.group(1)), True)
        c.used.append(m.span())


def _parse_high(c):
    """52주 고점 대비 X% 이내 / X% 이상 하락"""
    for m in re.finditer(rf"(?:52주\s*)?(?:고점|최고가|신고가|전고점)\s*(?:대비|에서|보다|으로부터|부터)?[^\d]{{0,6}}?{NUM}\s*(?:%|퍼센트|프로)",
                         c.t):
        a, b = m.span(1)
        tail = _window_after(c.t, m.end(), 12)
        v = abs(float(m.group(1)))
        if re.search(r"이내|안쪽|근처|부근|이하|미만|안\b|까지", tail):
            c.set("near_high_pct", v, True)
        else:
            c.set("far_from_high_pct", v, True)
        c.used.append((a, m.end()))


_PERIODS = [
    (r"오늘|금일|당일|하루|장중", "today"),
    (r"1\s*개월|한\s*달|한달|4\s*주|최근\s*한\s*달|1\s*달|지난\s*달", "1m"),
    (r"3\s*개월|석\s*달|세\s*달|분기|최근\s*3\s*개월", "3m"),
    (r"1\s*년|일\s*년|연간|12\s*개월|올해|올\s*들어|작년\s*대비|1년간", "1y"),
]


def _period_near(t, start, end):
    """숫자 앞뒤에서 기간 표현을 찾는다. 없으면 None."""
    zone = t[max(0, start - 18):min(len(t), end + 6)]
    best = None
    for pat, key in _PERIODS:
        for m in re.finditer(pat, zone):
            dist = abs((max(0, start - 18) + m.start()) - start)
            if best is None or dist < best[0]:
                best = (dist, key)
    return best[1] if best else None


def _parse_returns(c):
    """'1년간 50% 넘게 오른', '한 달 새 10% 이상 빠진', '1년 수익률 -20% 이하', '오늘 3% 이상 급등'"""
    for m in re.finditer(rf"{NUM}\s*(?:%|퍼센트|프로)", c.t):
        a, b = m.span(1)
        if c.used_at(a, b):
            continue
        tail = _window_after(c.t, m.end(), 16)
        head = c.t[max(0, a - 10):a]
        v = float(m.group(1))
        down = re.search(DOWN_WORDS, tail) is not None
        up = re.search(UP_WORDS, tail) is not None or "수익률" in head or "상승률" in head
        if not (down or up or re.search(r"수익률|상승률|하락률|등락", head)):
            continue  # 수익률 맥락이 아니면 건드리지 않음
        period = _period_near(c.t, a, b)
        if period is None:
            period = "1y"
            c.notes.append("기간이 없어 1년 수익률로 해석했습니다")
        key = {"today": "change_pct", "1m": "ret_1m", "3m": "ret_3m", "1y": "ret_1y"}[period]
        d = _cmp(c.t, a, m.end())
        if down and v > 0:
            # "10% 이상 빠진" → 수익률 ≤ -10 / "10% 이내로 빠진" → ≥ -10
            if d == "le":
                c.set(f"{key}_min", -v, True)
            else:
                c.set(f"{key}_max", -v, True)
        else:
            c.set(f"{key}_{'max' if d == 'le' else 'min'}", v, True)
        c.used.append((a, m.end()))


# ======================= 국가·테마·종목 =======================
def _excluded_after(compact, end):
    return re.match(rf"(?:은|는|이|가|주|종목|쪽)?(?:{EXCLUDE_WORDS})", compact[end:end + 8]) is not None


def _parse_countries(c, compact):
    groups = {
        "KR": r"한국|국내|코스피|코스닥|kospi|kosdaq|국장|한국주식",
        "US": r"미국|해외|나스닥|뉴욕|s&p|nasdaq|미장|미국주식|usa|(?<![a-z])us(?![a-z])",
    }
    inc, exc = [], []
    for code, pat in groups.items():
        for m in re.finditer(pat, compact):
            (exc if _excluded_after(compact, m.end()) else inc).append(code)
    if inc:
        c.set("countries", sorted(set(inc) - set(exc)), True)
    if exc:
        c.set("exclude_countries", sorted(set(exc)), True)


def _parse_names(c, compact, covered):
    """종목 이름·별칭·티커. 찾은 위치는 covered에 기록해 테마 단어와 겹치지 않게 한다."""
    found = []
    cands = [(r["name"].lower().replace(" ", ""), r["code"]) for r in UNIVERSE]
    cands += [(k, v) for k, v in ALIASES.items() if v]
    for name, code in sorted(cands, key=lambda x: -len(x[0])):
        if len(name) < 2:
            continue
        for m in re.finditer(re.escape(name), compact):
            if any(m.start() < e and s < m.end() for s, e in covered):
                continue
            # 짧은 영문 별칭은 단어 경계 확인
            if name.isascii() and len(name) <= 4:
                if (m.start() > 0 and compact[m.start() - 1].isalpha()) or \
                        (m.end() < len(compact) and compact[m.end()].isascii() and compact[m.end()].isalpha()):
                    continue
            covered.append(m.span())
            found.append((code, _excluded_after(compact, m.end())))
    # 대문자 티커 (원문 기준)
    for m in re.finditer(r"\b[A-Z]{2,5}\b", c.raw):
        tk = m.group(0)
        if tk in _BY_CODE and tk not in _TICKER_STOP and all(code != tk for code, _ in found):
            found.append((tk, False))
    return found


def _parse_themes(c, compact, covered):
    inc, exc = [], []
    kws = []
    for theme, spec in THEMES.items():
        for kw in [theme.lower().replace(" ", "")] + [k.lower().replace(" ", "") for k in spec["keywords"]]:
            kws.append((kw, theme))
    for kw, theme in sorted(kws, key=lambda x: -len(x[0])):
        for m in re.finditer(re.escape(kw), compact):
            if any(m.start() < e and s < m.end() for s, e in covered):
                continue
            if kw.isascii():  # 영문 키워드는 단어 경계
                left = compact[m.start() - 1] if m.start() > 0 else " "
                right = compact[m.end()] if m.end() < len(compact) else " "
                if (left.isascii() and left.isalpha()) or (right.isascii() and right.isalpha()):
                    continue
            covered.append(m.span())
            (exc if _excluded_after(compact, m.end()) else inc).append(theme)
    return list(dict.fromkeys(inc)), list(dict.fromkeys(exc))


_FUZZY_STOP = {"종목", "회사", "기업", "주식", "관련", "비교", "같은", "비슷한", "추천", "찾아", "보여",
               "알려", "중에", "중에서", "위주", "정도", "이상", "이하", "사이", "요즘", "최근", "오늘",
               "올해", "가장", "제일", "많이", "높은", "낮은", "좋은", "개월", "수익률", "시총", "배당"}


def _fuzzy(c, compact, covered):
    """오타 보정: 인식 못 한 단어를 테마·종목 이름과 비교."""
    vocab = {}
    for theme, spec in THEMES.items():
        for kw in [theme] + spec["keywords"]:
            if len(kw) >= 2 and not kw.isascii():
                vocab[kw.replace(" ", "")] = ("theme", theme)
    for r in UNIVERSE:
        if not r["name"].isascii():
            vocab[r["name"].replace(" ", "")] = ("code", r["code"])
    themes, codes = [], []
    for tok in re.findall(r"[가-힣]{2,}", c.t):
        tok = _strip_josa(tok)
        if len(tok) < 2 or tok in vocab:
            continue
        if tok in _FUZZY_STOP:
            continue
        # 세 글자 이상은 한 글자 오타(유사도 약 0.67)까지 허용
        hit = difflib.get_close_matches(tok, vocab.keys(), n=1, cutoff=0.66 if len(tok) >= 3 else 0.8)
        if hit:
            kind, val = vocab[hit[0]]
            (themes if kind == "theme" else codes).append(val)
            c.notes.append(f"'{tok}'를 '{hit[0]}'(으)로 이해했습니다")
    return themes, codes


# ======================= 개념어 =======================
_CONCEPTS = [
    # (표현, 조건, 기본 정렬)
    (r"저평가|싼\s*주식|싸게|가치주|밸류|저per", {"per_max": 15, "per_min": 0.01}, ("per", True)),
    (r"고평가|비싼", {"per_min": 30}, None),
    (r"저pbr|자산주|청산가치|장부가", {"pbr_max": 1}, ("pbr", True)),
    (r"배당\s*(?:성장|증가)(?![^\d]{0,8}순)|배당을?\s*꾸준히\s*(?:늘|올)|배당\s*귀족|배당\s*킹", {"div_cagr_5y_min": 5}, ("div_cagr_5y", False)),
    (r"고배당|배당주|배당\s*(?:많|높|좋)(?!\S*\s*순)", {"div_min": 3}, ("div_yield", False)),
    (r"초대형|메가캡|시총\s*최상위", {"market_cap_min": 100}, ("market_cap_jo", False)),
    (r"대형주|대형|우량주|블루칩|대장주", {"market_cap_min": 10}, ("market_cap_jo", False)),
    (r"중형주", {"market_cap_min": 1, "market_cap_max": 10}, None),
    (r"소형주|스몰캡", {"market_cap_max": 1}, None),
    (r"중소형", {"market_cap_max": 5}, None),
    (r"신고가|고점\s*(?:근처|부근|돌파)|최고가\s*(?:근처|경신)|전고점", {"near_high_pct": 5}, ("from_high_pct", False)),
    (r"낙폭\s*과대|많이\s*(?:빠진|떨어진|하락한)|폭락|바닥|저점|반토막|물린", {"far_from_high_pct": 25}, ("from_high_pct", True)),
    (r"반등|턴어라운드|회복", {"far_from_high_pct": 15, "ret_1m_min": 0}, ("ret_1m", False)),
    (r"모멘텀|상승세|상승\s*추세|추세|강세|잘\s*나가|핫한|주도주|급등주", {"ret_3m_min": 15}, ("ret_3m", False)),
    (r"하락세|약세|부진|소외", {"ret_3m_max": -10}, ("ret_3m", True)),
    (r"흑자|적자\s*(?:제외|아닌|빼고)|이익\s*내는|돈\s*버는|수익성", {"per_min": 0.01}, None),
]


def _parse_concepts(c):
    sort_hint = None
    for pat, cond, sort in _CONCEPTS:
        if re.search(pat, c.t):
            for k, v in cond.items():
                c.set(k, v)  # 수치로 직접 쓴 조건이 우선
            if sort and sort_hint is None:
                sort_hint = sort
    return sort_hint


# ======================= 정렬·개수 =======================
def _parse_sort(c, period_hint):
    t = c.t
    rules = [
        (r"(?:시총|시가총액|덩치|규모).{0,4}(?:큰|높은|많은)\s*순", ("market_cap_jo", False)),
        (r"(?:시총|시가총액|덩치|규모).{0,4}(?:작은|낮은)\s*순", ("market_cap_jo", True)),
        (r"per.{0,4}(?:낮은|싼)\s*순|싼\s*순|저평가\s*순", ("per", True)),
        (r"per.{0,4}높은\s*순|비싼\s*순", ("per", False)),
        (r"pbr.{0,4}(?:낮은|싼)\s*순", ("pbr", True)),
        (r"배당\s*(?:성장률|성장|증가율).{0,6}(?:높은|큰|좋은)\s*순", ("div_cagr_5y", False)),
        (r"배당.{0,6}(?:높은|많은|큰|좋은)\s*순", ("div_yield", False)),
        (r"(?:오늘|금일|당일).{0,8}(?:많이\s*)?(?:오른|상승|급등|강한)", ("change_pct", False)),
        (r"(?:오늘|금일|당일).{0,8}(?:많이\s*)?(?:빠진|하락|떨어진|내린|급락|약한)", ("change_pct", True)),
        (r"(?:고점|최고가).{0,8}(?:많이\s*)?(?:빠진|떨어진|하락한)\s*순", ("from_high_pct", True)),
        (r"(?:많이|가장|제일|크게)\s*(?:오른|상승한|뛴)|수익률.{0,4}(?:높은|좋은)\s*순|상승률\s*순", ("RET", False)),
        (r"(?:많이|가장|제일|크게)\s*(?:빠진|떨어진|하락한)|수익률.{0,4}(?:낮은|나쁜)\s*순", ("RET", True)),
    ]
    for pat, (key, asc) in rules:
        if re.search(pat, t):
            if key == "RET":
                key = {"today": "change_pct", "1m": "ret_1m", "3m": "ret_3m"}.get(period_hint, "ret_1y")
            c.set("sort_by", key, True)
            c.set("ascending", asc, True)
            return True
    return False


def _parse_limit(c):
    t = c.t
    m = re.search(r"(?:상위|top|베스트|탑|톱)\s*(\d+)", t) or re.search(r"(\d+)\s*(?:개(?!월)|종목|가지)", t)
    if m:
        c.set("limit", int(m.group(1)), True)
        return
    # 앞 글자가 한글이면 수사가 아님 ("핫한 종목"의 '한')
    m = re.search(r"(?<![가-힣])(한|하나|두|둘|세|셋|네|넷|다섯|여섯|일곱|여덟|아홉|열|스무)\s*(?:개(?!월)|종목|가지)", t)
    if m:
        c.set("limit", KOR_NUM[m.group(1)], True)


# ======================= 본체 =======================
def rule_parse(text: str, fx: float = 1400.0) -> dict:
    c = _Ctx(text, fx)
    compact = c.t.replace(" ", "")

    # 1) 숫자가 걸린 조건 (먼저 처리해 숫자 위치를 기록)
    _parse_div_streak(c)
    _parse_high(c)
    _parse_mcap(c)
    for prefix, metric_re, unit_re in _METRICS:
        _parse_metric(c, prefix, metric_re, unit_re)
    _parse_returns(c)

    # 2) 국가·종목·테마
    _parse_countries(c, compact)
    covered = []
    names = _parse_names(c, compact, covered)
    themes_in, themes_out = _parse_themes(c, compact, covered)
    if not names and not themes_in and not themes_out:
        fz_themes, fz_codes = _fuzzy(c, compact, covered)
        themes_in += fz_themes
        names += [(code, False) for code in fz_codes]

    inc_codes = [code for code, ex in names if not ex]
    exc_codes = [code for code, ex in names if ex]
    similar = re.search(SIMILAR_WORDS, c.t) is not None
    if inc_codes and similar:
        # "엔비디아 같은 종목" → 그 종목의 테마에서, 본인은 제외
        sim_themes = []
        for code in inc_codes:
            sim_themes += _BY_CODE[code]["themes"]
        themes_in = list(dict.fromkeys(themes_in + sim_themes))
        exc_codes += inc_codes
        names_txt = ", ".join(_BY_CODE[x]["name"] for x in inc_codes)
        c.notes.append(f"{names_txt}의 테마({', '.join(dict.fromkeys(sim_themes))})에서 다른 종목을 찾습니다")
    elif inc_codes:
        c.set("codes", inc_codes, True)
    if exc_codes:
        c.set("exclude_codes", list(dict.fromkeys(exc_codes)), True)
    if themes_in:
        c.set("themes", themes_in, True)
    if themes_out:
        c.set("exclude_themes", themes_out, True)

    # 3) 개념어, 정렬, 개수
    sort_hint = _parse_concepts(c)
    period_hint = None
    for pat, key in _PERIODS:
        if re.search(pat, c.t):
            period_hint = key
            break
    if not _parse_sort(c, period_hint) and sort_hint:
        c.set("sort_by", sort_hint[0])
        c.set("ascending", sort_hint[1])
    _parse_limit(c)

    f = c.f
    f["_notes"] = c.notes
    return f


# ======================= Claude API =======================
_NUM_FIELDS = ["market_cap_min", "market_cap_max", "per_min", "per_max", "pbr_min", "pbr_max", "div_min",
               "change_pct_min", "change_pct_max", "ret_1m_min", "ret_1m_max", "ret_3m_min", "ret_3m_max",
               "ret_1y_min", "ret_1y_max", "near_high_pct", "far_from_high_pct",
               "div_cagr_5y_min", "div_cagr_5y_max", "div_up_years_min"]

_FILTER_TOOL = {
    "name": "set_filters",
    "description": "사용자의 종목 검색 문장을 스크리너 필터로 변환한다. 언급되지 않은 조건은 넣지 않는다.",
    "input_schema": {
        "type": "object",
        "properties": {
            "countries": {"type": "array", "items": {"type": "string", "enum": ["KR", "US"]}},
            "exclude_countries": {"type": "array", "items": {"type": "string", "enum": ["KR", "US"]}},
            "themes": {"type": "array", "items": {"type": "string", "enum": list(THEMES.keys())}},
            "exclude_themes": {"type": "array", "items": {"type": "string", "enum": list(THEMES.keys())}},
            "codes": {"type": "array", "items": {"type": "string", "enum": list(_BY_CODE)},
                      "description": "특정 종목만 볼 때"},
            "exclude_codes": {"type": "array", "items": {"type": "string", "enum": list(_BY_CODE)}},
            **{k: {"type": "number"} for k in _NUM_FIELDS},
            "sort_by": {"type": "string", "enum": ["market_cap_jo", "per", "pbr", "div_yield", "change_pct",
                                                    "ret_1m", "ret_3m", "ret_1y", "from_high_pct",
                                                    "div_cagr_5y", "div_up_years"]},
            "ascending": {"type": "boolean"},
            "limit": {"type": "integer"},
            "explanation": {"type": "string", "description": "어떻게 해석했는지 한국어 한두 문장"},
        },
    },
}


AVAILABLE_DATA = (
    "현재가, 오늘 등락률(change_pct), 시가총액(조 원), PER, PBR, 배당수익률(div_yield %), "
    "5년 연평균 배당성장률(div_cagr_5y %), 배당 연속 증가 연수(div_up_years), "
    "1개월·3개월·1년 수익률(ret_1m/ret_3m/ret_1y %), 52주 고점 대비(from_high_pct %), 테마, 국가"
)
MISSING_DATA = "매출·영업이익·순이익 성장률, 부채비율, ROE, 수급(외국인·기관), 뉴스, 애널리스트 목표가"


def _system_prompt():
    names = ", ".join(f'{r["name"]}({r["code"]})' for r in UNIVERSE)
    return (
        "너는 주식 스크리너의 조건 해석기다. 사용자의 문장을 set_filters 도구 입력으로 바꿔라.\n"
        f"보유 데이터: {AVAILABLE_DATA}.\n"
        f"없는 데이터: {MISSING_DATA}. 이런 조건을 요청받으면 explanation에 '해당 데이터가 없다'고 분명히 적고, "
        "의미가 가장 가까운 보유 데이터로 대체할 수 있으면 대체한 뒤 무엇으로 대체했는지 적어라.\n"
        "단위: market_cap은 조 원(달러는 약 1,400원으로 환산), 수익률·배당·등락은 %(하락은 음수), "
        "near_high_pct는 52주 고점에서 이 % 이내, far_from_high_pct는 고점 대비 이 % 이상 하락.\n"
        "모호한 표현 기준: 저평가→PER 0.01~15, 고배당→배당 3% 이상, 배당성장→5년 배당성장률 5% 이상, "
        "대형주→시총 10조 이상, 소형주→1조 이하, 모멘텀→3개월 15% 이상, "
        "반등→고점 대비 15% 이상 하락이면서 1개월 0% 이상, 흑자→PER 0.01 이상.\n"
        "'X 같은/비슷한' 종목이면 X의 테마로 찾고 X는 exclude_codes에 넣는다. "
        "특정 종목에 대한 질문(예: '삼성전자 지금 어때')이면 codes에 그 종목을 넣는다.\n"
        f"검색 가능한 종목: {names}\n"
        "목록에 없는 종목·테마면 가장 가까운 것을 고르고 explanation에 그 사실을 적는다. "
        "explanation은 한국어 한두 문장."
    )


def claude_parse(text: str, api_key: str, model: str) -> dict:
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model=model,
        max_tokens=800,
        system=[{"type": "text", "text": _system_prompt(), "cache_control": {"type": "ephemeral"}}],
        tools=[_FILTER_TOOL],
        tool_choice={"type": "tool", "name": "set_filters"},
        messages=[{"role": "user", "content": text}],
    )
    for block in msg.content:
        if block.type == "tool_use":
            return dict(block.input)
    raise RuntimeError("필터를 해석하지 못했습니다: " + json.dumps([b.type for b in msg.content]))


def parse(text: str, api_key: str = None, model: str = "claude-sonnet-5-5", fx: float = 1400.0):
    """(filters, 사용한 방식, 해석 메모 목록) 반환."""
    if api_key:
        try:
            f = claude_parse(text, api_key, model)
            note = f.pop("explanation", "")
            return f, "Claude", [note] if note else []
        except Exception as e:
            f = rule_parse(text, fx)
            notes = f.pop("_notes", [])
            return f, "규칙", [f"Claude 호출 실패로 규칙 해석기 사용 ({e})"] + notes
    f = rule_parse(text, fx)
    return f, "규칙", f.pop("_notes", [])
