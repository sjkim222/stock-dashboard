"""문장 해석기 회귀 테스트. 실행: python tests/test_nl_parser.py"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import nl_parser

# (문장, 기대 조건 일부) — 기대 키가 모두 맞으면 통과
CASES = [
 ("PER 15 이하인 미국 반도체주", {"countries":["US"],"themes":["반도체"],"per_max":15}),
 ("PER이 15를 넘지 않는 한국 종목", {"countries":["KR"],"per_max":15}),
 ("PER 10~20 사이 금융주", {"per_min":10,"per_max":20,"themes":["금융"]}),
 ("배당 3% 이상 한국 금융주", {"countries":["KR"],"themes":["금융"],"div_min":3}),
 ("배당수익률 최소 4%", {"div_min":4}),
 ("시총 10조 이상 바이오", {"market_cap_min":10,"themes":["바이오·헬스케어"]}),
 ("시가총액 5천억 이하 반도체 장비주", {"market_cap_max":0.5,"themes":["반도체 장비"]}),
 ("시총 1조에서 10조 사이", {"market_cap_min":1,"market_cap_max":10}),
 ("시총 1000억 달러 넘는 미국 기업", {"countries":["US"],"market_cap_min":140}),
 ("1년간 50% 넘게 오른 방산주", {"ret_1y_min":50,"themes":["방산"]}),
 ("한 달 새 10% 이상 빠진 2차전지", {"ret_1m_max":-10,"themes":["2차전지·전기차"]}),
 ("3개월 수익률 20% 이상", {"ret_3m_min":20}),
 ("1년 수익률 -20% 이하", {"ret_1y_max":-20}),
 ("오늘 3% 이상 급등한 종목", {"change_pct_min":3}),
 ("오늘 많이 빠진 종목 5개", {"sort_by":"change_pct","ascending":True,"limit":5}),
 ("고점 대비 30% 이상 빠진 반도체", {"far_from_high_pct":30,"themes":["반도체"]}),
 ("52주 고점에서 10% 이내 종목", {"near_high_pct":10}),
 ("신고가 근처 방산주", {"near_high_pct":5,"themes":["방산"]}),
 ("저평가 우량주", {"per_max":15,"market_cap_min":10}),
 ("고배당 미국주 배당 높은 순", {"div_min":3,"countries":["US"],"sort_by":"div_yield"}),
 ("모멘텀 좋은 AI주", {"ret_3m_min":15,"themes":["AI·빅테크"]}),
 ("낙폭과대 반등 노릴 만한 2차전지", {"themes":["2차전지·전기차"],"ret_1m_min":0}),
 ("흑자인 소형주", {"per_min":0.01,"market_cap_max":1}),
 ("엔비디아 같은 종목", {"themes":["반도체"],"exclude_codes":["NVDA"]}),
 ("삼전이랑 하닉 비교", {"codes":["005930","000660"]}),
 ("테슬라랑 비슷한 회사", {"exclude_codes":["TSLA"]}),
 ("반도체 빼고 한국 대형주", {"exclude_themes":["반도체"],"countries":["KR"],"market_cap_min":10}),
 ("미국 말고 배당주", {"exclude_countries":["US"],"div_min":3}),
 ("AI 빅테크 중 테슬라 제외", {"themes":["AI·빅테크"]}),
 ("반도채 종목", {"themes":["반도체"]}),
 ("2 차전지 대형주 상위 3개", {"themes":["2차전지·전기차"],"market_cap_min":10,"limit":3}),
 ("HBM 관련주", {"themes":["반도체"]}),
 ("원전 smr 관련주", {"themes":["전력·AI인프라"]}),
 ("한국항공우주 차트", {"codes":["047810"]}),
 ("NVDA AMD 비교", {"codes":["NVDA","AMD"]}),
 ("PBR 1 이하 은행주", {"pbr_max":1,"themes":["금융"]}),
 ("저PBR 자산주", {"pbr_max":1}),
 ("1년 동안 많이 오른 순으로 세 종목", {"sort_by":"ret_1y","limit":3}),
 ("시총 큰 순 10개 미국 반도체", {"sort_by":"market_cap_jo","limit":10,"countries":["US"]}),
 ("3개월 상위 5개", {"limit":5}),
 ("에코프로비엠 말고 2차전지", {"exclude_codes":["247540"],"themes":["2차전지·전기차"]}),
 ("LS ELECTRIC 같은 전력기기", {"themes":["전력·AI인프라"]}),
]
CASES += [
 ("요즘 핫한 종목 추천", {"ret_3m_min":15, "limit":None}),
 ("한 종목만 보여줘 시총 큰 순", {"limit":1}),
 ("열 종목 배당 높은 순", {"limit":10,"sort_by":"div_yield"}),
 ("우세 종목", {"limit":None}),
]
CASES += [
 ("반도체 빼고 한국 대형주 배당 높은 순", {"div_min":None,"sort_by":"div_yield","exclude_themes":["반도체"]}),
 ("배당 많이 주는 회사", {"div_min":3}),
 ("배당 좋은 미국주", {"div_min":3,"countries":["US"]}),
]

CASES += [
 ("배당성장률 높은 미국주", {"div_cagr_5y_min":5,"countries":["US"],"sort_by":"div_cagr_5y"}),
 ("배당 성장률 10% 이상", {"div_cagr_5y_min":10}),
 ("배당성장률 높은 순", {"div_cagr_5y_min":None,"sort_by":"div_cagr_5y"}),
 ("5년 연속 배당 증가한 종목", {"div_up_years_min":5}),
 ("배당을 10년 이상 연속 늘린 기업", {"div_up_years_min":10}),
 ("배당 꾸준히 늘리는 고배당주", {"div_cagr_5y_min":5,"div_min":3}),
 ("배당수익률 3% 이상이고 배당성장률 7% 이상", {"div_min":3,"div_cagr_5y_min":7}),
]


def run():
    fail = 0
    for text, exp in CASES:
        f, _, notes = nl_parser.parse(text, fx=1400)
        bad = {}
        for k, v in exp.items():
            got = f.get(k)
            if isinstance(v, list) and isinstance(got, list):
                got, v = sorted(got), sorted(v)
            if got != v:
                bad[k] = (got, v)
        if bad:
            fail += 1
            print("실패:", text, bad, notes)
    print(f"{len(CASES) - fail}/{len(CASES)} 통과")
    return fail == 0


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
