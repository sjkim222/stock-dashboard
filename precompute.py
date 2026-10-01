"""
종목 지표 사전 계산 스크립트 — GitHub Actions가 정해진 시각마다 실행한다.

결과물 (출력 폴더에 저장 → 워크플로가 저장소의 data 브랜치에 올림)
  metrics.csv : 종목별 현재가·시총·PER·PBR·배당·수익률·52주 고점 대비
  meta.json   : 계산 시각, 환율, 데이터원, 종목 수

키(KIS_APP_KEY, KIS_APP_SECRET)는 환경변수로 받는다. 없으면 야후로 계산한다.
로컬 실행:  python precompute.py --out out
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

import market
from kis import KISClient
from themes import build_universe


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="out")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    key = os.environ.get("KIS_APP_KEY", "").strip()
    sec = os.environ.get("KIS_APP_SECRET", "").strip()
    kis = None
    if key and sec:
        kis = KISClient(key, sec, demo=os.environ.get("KIS_DEMO", "false").lower() == "true")
        try:
            kis.token()
            print("한국투자증권 API로 계산합니다.")
        except Exception as e:
            print(f"증권사 토큰 발급 실패 → 야후로 계산합니다: {e}")
            kis = None
    else:
        print("증권사 키가 없어 야후로 계산합니다.")

    universe = build_universe()
    t0 = time.time()
    df, fx = market.build_metrics(universe, kis)
    elapsed = time.time() - t0

    filled = int(df["price"].notna().sum())
    print(f"{len(df)}개 종목 중 {filled}개 계산, {elapsed:.0f}초 소요")
    print(df["source"].value_counts().to_string())
    if filled < len(df) * 0.5:
        # 대부분 실패했으면 기존 데이터를 덮어쓰지 않도록 실패 처리
        print("절반 이상 실패 → 저장하지 않습니다.")
        sys.exit(1)

    # 저장된 값은 계산 시점 스냅샷이므로 출처 표시를 바꿔 둔다 (사이트에서 실시간 갱신 시 다시 바뀜)
    df["source"] = df["source"].replace({"KIS 실시간": "KIS 사전계산", "Yahoo 지연": "Yahoo 사전계산"})
    df.to_csv(out / "metrics.csv", index=False, encoding="utf-8")
    (out / "meta.json").write_text(json.dumps({
        "ts": time.time(),
        "fx": fx,
        "source": "KIS" if kis else "Yahoo",
        "count": len(df),
        "filled": filled,
        "seconds": round(elapsed),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"저장 완료 → {out.resolve()}")


if __name__ == "__main__":
    main()
