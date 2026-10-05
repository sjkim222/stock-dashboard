"""
종목 지표 사전 계산 스크립트 — GitHub Actions가 정해진 시각마다 실행한다.

결과물 (출력 폴더에 저장 → 워크플로가 저장소의 data 브랜치에 올림)
  metrics.csv / meta.json : 야후 기준 지표. 공개 파일, 모든 방문자가 봄
  metrics_kis.enc         : 한국투자증권 기준 지표. KIS_APP_SECRET으로 암호화, 관리자만 풀 수 있음

출력 폴더에 이전 파일이 있으면, 이번 계산이 실패한 쪽은 이전 파일을 그대로 둔다.
키(KIS_APP_KEY, KIS_APP_SECRET)는 환경변수로 받는다. 없으면 야후 파일만 만든다.
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
from secure_data import encrypt_payload
from themes import build_universe

PUBLIC_CSV, PUBLIC_META, KIS_FILE = "metrics.csv", "meta.json", "metrics_kis.enc"


def compute(universe, kis, label):
    t0 = time.time()
    df, fx = market.build_metrics(universe, kis)
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

    # ---- 1) 야후용 (공개) ----
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
    res = compute(universe, None, "야후")
    if res:
        df, meta = res
        df["source"] = "Yahoo 사전계산"
        df.to_csv(out / PUBLIC_CSV, index=False, encoding="utf-8")
        (out / PUBLIC_META).write_text(json.dumps({**meta, "source": "Yahoo"}, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
        ok.append("야후")

    # ---- 2) 증권사용 (암호화) ----
    key = os.environ.get("KIS_APP_KEY", "").strip()
    sec = os.environ.get("KIS_APP_SECRET", "").strip()
    if key and sec:
        kis = KISClient(key, sec, demo=os.environ.get("KIS_DEMO", "false").lower() == "true")
        try:
            kis.token()
            res = compute(universe, kis, "증권사")
        except Exception as e:
            print(f"[증권사] 토큰 발급 실패 → 이 파일은 갱신하지 않습니다: {e}")
            res = None
        if res:
            df, meta = res
            df["source"] = df["source"].replace({"KIS 실시간": "KIS 사전계산", "Yahoo 지연": "Yahoo 사전계산"})
            token = encrypt_payload(sec, {**meta, "source": "KIS"}, df.to_csv(index=False))
            (out / KIS_FILE).write_bytes(token)
            ok.append("증권사(암호화)")
    else:
        print("[증권사] 키가 없어 건너뜁니다.")

    if not ok:
        print("두 계산 모두 실패했습니다.")
        sys.exit(1)
    print(f"저장 완료 ({', '.join(ok)}) → {out.resolve()}")
    for p in sorted(out.iterdir()):
        if p.is_file():
            print(f"  {p.name}  {p.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
