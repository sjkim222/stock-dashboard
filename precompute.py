"""
종목 지표 사전 계산 스크립트 — GitHub Actions가 정해진 시각마다 실행한다.

결과물 (출력 폴더에 저장 → 워크플로가 저장소의 data 브랜치에 올림)
  metrics.csv / meta.json : 야후 기준 지표. 공개 파일, 모든 방문자와 관리자가 봄

한국투자증권 API는 호출하지 않는다 (자동 호출 0번). 증권사 데이터는 사이트에서
관리자 PIN을 넣고 볼 때만 받는다.
계산이 실패하면 출력 폴더의 이전 파일을 그대로 둔다.
로컬 실행:  python precompute.py --out out
"""
import argparse
import json
import sys
import time
from pathlib import Path

import market
from themes import build_universe

PUBLIC_CSV, PUBLIC_META = "metrics.csv", "meta.json"
OLD_KIS_FILE = "metrics_kis.enc"  # 예전 구조의 증권사 기준 암호화 파일 (이제 만들지 않음)


def compute(universe, label):
    t0 = time.time()
    df, fx = market.build_metrics(universe, None)
    elapsed = time.time() - t0
    filled = int(df["price"].notna().sum())
    print(f"[{label}] {len(df)}개 종목 중 {filled}개 계산, {elapsed:.0f}초 소요")
    print(df["source"].value_counts().to_string())
    if filled < len(df) * 0.5:
        print(f"[{label}] 절반 이상 실패 → 이 파일은 갱신하지 않습니다.")
        return None
    meta = {"ts": time.time(), "fx": fx, "count": len(df), "filled": filled, "seconds": round(elapsed)}
    return df, meta


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="out")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    universe = build_universe()
    ok = []

    # ---- 1) 야후 기준 (공개) ----
    # 이전 구조에서 공개 파일에 증권사 데이터가 들어 있었다면 먼저 지운다
    old_meta = out / PUBLIC_META
    if old_meta.exists():
        try:
            if json.loads(old_meta.read_text(encoding="utf-8")).get("source") != "Yahoo":
                (out / PUBLIC_CSV).unlink(missing_ok=True)
                old_meta.unlink()
                print("공개 파일에 있던 증권사 기준 데이터를 삭제했습니다.")
        except Exception:
            pass
    res = compute(universe, "야후")
    if res:
        df, meta = res
        df["source"] = "Yahoo 사전계산"
        df.to_csv(out / PUBLIC_CSV, index=False, encoding="utf-8")
        (out / PUBLIC_META).write_text(json.dumps({**meta, "source": "Yahoo"}, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
        ok.append("야후")

    # ---- 2) 예전 증권사용 암호화 파일 정리 ----
    if (out / OLD_KIS_FILE).exists():
        (out / OLD_KIS_FILE).unlink()
        print("예전 증권사 기준 암호화 파일을 삭제했습니다.")

    if not ok:
        print("계산에 실패했습니다.")
        sys.exit(1)
    print(f"저장 완료 ({', '.join(ok)}) → {out.resolve()}")
    for p in sorted(out.iterdir()):
        if p.is_file():
            print(f"  {p.name}  {p.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
