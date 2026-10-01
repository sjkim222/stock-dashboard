# 글로벌 주식 대시보드

한국·미국 대표지수를 한눈에 보고, 섹터·테마 / 수치 조건 / 문장으로 원하는 종목을 찾는 Streamlit 앱입니다.

## 화면 구성

| 탭 | 기능 |
|---|---|
| 🌏 대표지수 | KOSPI·KOSDAQ·KOSPI200 / S&P500·NASDAQ·다우·필라델피아 반도체 현재값과 등락률, 지수 차트 |
| 🔍 종목 찾기 | ① 문장으로 ("PER 15 이하인 미국 반도체주") ② 섹터·테마 선택 ③ 슬라이더 수치 조건. 결과 행을 클릭하면 차트가 열림 |
| 📊 종목 상세 | 등록 종목 선택 또는 코드 직접 입력(005930, AAPL) → 현재가, 캔들 차트, 20·60일 이동평균 |

## 1. 실행 (키 없이도 바로 동작)

```bash
pip install -r requirements.txt
streamlit run app.py
```

브라우저에 `http://localhost:8501` 이 열립니다. 이 상태에서는 야후 파이낸스의 **지연 시세(약 15~20분)** 와 **규칙 기반 문장 해석**으로 동작합니다.

## 2. 한국투자증권 API 연결 (주 데이터원)

키를 넣으면 앱의 데이터원이 야후에서 **한국투자증권 API**로 바뀝니다.

| 항목 | 키 연결 시 | 키 없을 때 |
|---|---|---|
| 국내 지수 (KOSPI·KOSDAQ·KOSPI200) | 한국투자증권 실시간 | 야후 지연 |
| 미국 지수 (S&P500·나스닥·다우·SOX) | 한국투자증권 (코드 인식 실패 시 야후) | 야후 지연 |
| 국내·미국 종목 현재가, 차트 | 한국투자증권 실시간 | 야후 지연 |
| PER·PBR·시가총액·52주 고점 | 한국투자증권 | 야후 |
| 배당수익률 | 야후에서 보충 (못 받으면 빈칸) | 야후 |
| 5년 배당성장률, 배당 연속 증가 연수 | 야후 배당 이력으로 계산 | 야후 |

설정 방법
1. 한국투자증권 계좌 개설 (비대면 가능)
2. KIS Developers(apiportal.koreainvestment.com)에서 Open API 서비스 신청 → APP KEY, APP SECRET 발급
3. 배포 사이트: Streamlit 앱 화면 오른쪽 아래 **Manage app → Settings → Secrets** 에 아래처럼 입력 후 저장
   (내 PC에서 실행할 때는 `.streamlit/secrets.toml` 에 같은 내용)

```toml
KIS_APP_KEY = "발급받은 APP KEY"
KIS_APP_SECRET = "발급받은 APP SECRET"
KIS_DEMO = "false"
```

참고
- 미국 주식 실시간 시세는 한국투자증권 앱에서 **해외 실시간 시세 신청**이 필요할 수 있습니다. 신청 전에는 지연 시세가 올 수 있습니다.
- 모의투자 키(`KIS_DEMO = "true"`)는 초당 호출 한도가 낮아 종목 찾기 첫 로딩이 몇 분 걸립니다. 실전 키를 권장합니다. 시세 조회만 하므로 주문은 일어나지 않습니다.
- 접근토큰은 하루 동안 재사용합니다. (재발급 빈도 제한 대응)

## 3. AI 모드 (Claude API)

`ANTHROPIC_API_KEY` 를 넣으면 종목 찾기 → 💬 문장으로 화면에 **🤖 AI 모드** 스위치가 켜집니다.

1. Claude가 자유로운 문장을 조건으로 바꿉니다. ("배당을 꾸준히 늘려 온 미국 배당주 추천해줘")
2. 없는 데이터(영업이익, 부채비율, 뉴스 등)를 물으면 없다고 밝히고 가장 가까운 조건으로 대체합니다.
3. 결과 표의 숫자만 근거로 대화체 해설을 씁니다. (눈에 띄는 종목, 상충 신호, 추가로 확인할 점)

비용: Claude API는 Claude 구독과 **별도 결제**입니다. 검색 1회(해석 + 해설)에 Sonnet 5.5 기준 약 20~30원입니다.
같은 질문은 하루 동안 다시 계산하지 않고, 스위치를 끄면 무료 규칙 해석기로 동작합니다.

설정
1. Claude Console(platform.claude.com)에서 가입 → 결제 수단·크레딧 등록 → API Keys에서 키 발급
2. Streamlit 앱 **Manage app → Settings → Secrets** 에 한 줄 추가

```toml
ANTHROPIC_API_KEY = "sk-ant-..."
CLAUDE_MODEL = "claude-sonnet-5-5"   # 비용을 줄이려면 "claude-haiku-4-5-20251001"
```

규칙 해석기(키 없을 때)가 알아듣는 표현은 화면의 "이런 표현을 알아들어요"에 정리되어 있습니다.
회귀 테스트: `python tests/test_nl_parser.py`

## 4. 종목·테마 추가

검색 대상은 `themes.py` 의 `THEMES` 에 등록된 종목(현재 약 100개)입니다. 원하는 테마를 새로 만들거나 종목을 추가하면 바로 반영됩니다.

```python
"로봇": {
    "keywords": ["로봇", "robot", "휴머노이드"],
    "KR": [("277810", "레인보우로보틱스", "KQ")],
    "US": [("ISRG", "인튜이티브서지컬", "NAS")],
},
```

## 5. 인터넷에 올리기 (선택)

GitHub에 올린 뒤 Streamlit Community Cloud(share.streamlit.io)에서 저장소를 연결하면 무료로 배포됩니다. 키는 저장소가 아니라 배포 화면의 **Secrets** 칸에 넣으세요. (`.gitignore` 에 `secrets.toml` 이 이미 제외되어 있습니다)

## 6. 종목 지표 미리 계산 (GitHub Actions)

종목 찾기 화면이 바로 뜨도록, GitHub이 정해진 시각에 지표를 계산해 저장소의 `data` 브랜치에 저장합니다. 사이트는 이 파일을 읽기만 하고, 현재가만 실시간으로 덮어씁니다.

- 실행 시각(한국시간): 평일 08:40, 12:10, 15:50 / 화~토 06:20 (미국 장 마감 후). GitHub 사정으로 몇 분~수십 분 늦을 수 있습니다.
- 설정: 저장소 **Settings → Secrets and variables → Actions → New repository secret** 에 `KIS_APP_KEY`, `KIS_APP_SECRET` 등록. 없으면 야후로 계산합니다.
- 바로 한 번 돌리기: 저장소 **Actions → 종목 지표 미리 계산 → Run workflow**
- 파일이 36시간 넘게 갱신되지 않으면 사이트가 예전처럼 직접 계산합니다.
- 실행 시각은 `.github/workflows/precompute.yml` 의 cron 줄에서 바꿀 수 있습니다 (UTC 기준).

## 7. 증권사 API 켜기/끄기

사이드바의 **한국투자증권 API 사용** 스위치로 켜고 끕니다. 끄면 지수·차트·현재가가 야후로 바뀝니다. 스위치는 지금 보고 있는 브라우저 화면에만 적용됩니다.

## 파일 구조

```
app.py          화면 (탭 3개)
themes.py       테마별 종목 목록, 대표지수 목록
market.py       시세·차트·지표 계산 (yfinance + KIS)
kis.py          한국투자증권 Open API 클라이언트
screener.py     조건 필터
nl_parser.py    문장 → 조건 변환 (Claude / 규칙)
precompute.py   지표 사전 계산 (GitHub Actions가 실행)
ai_commentary.py  AI 해설 (Claude)
tests/          문장 해석기 회귀 테스트
.github/workflows/precompute.yml  사전 계산 예약 설정
```

## 데이터 관련 주의

- PER은 최근 12개월 실적 기준이며 적자 기업은 PER 조건 검색에서 제외됩니다.
- 미국 종목 시가총액은 실시간 원/달러 환율로 환산해 한국 종목과 같은 기준(조 원)으로 비교합니다.
- 투자 판단의 최종 근거로 쓰기 전에 증권사 화면의 수치와 한 번 대조해 보세요.
