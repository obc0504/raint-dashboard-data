#!/usr/bin/env python3
"""전일 기준 일일 매출 비교 리포트 (전일 / 전주 동요일 / 전월 동일자).

사용: python3 -I scripts/daily_report.py [YYYY-MM-DD]
  인자 없으면 KST 기준 어제를 기준일로 사용.
집계 규칙은 README의 세션 기준과 동일:
  - 브랜드 없으면 '센티카'
  - 취소성 주문 제외(정확일치 CANCELED/CANCEL/RETURNED/RETURN/EXCHANGED,
    또는 취소/반품/교환/환불/결제오류 포함), 단 '반려' 포함 시 유지
"""
import json, sys, urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

BASE = "https://raw.githubusercontent.com/obc0504/raint-dashboard-data/main/"
FILES = ["cafe24", "wconcept", "29cm", "musinsa", "kakao", "qoo10",
         "gmarket_auction", "coupang_wing", "ohouse", "coupang_rocket"]
EXACT = {"CANCELED", "CANCEL", "RETURNED", "RETURN", "EXCHANGED"}
KEYWORDS = ["취소", "반품", "교환", "환불", "결제오류"]


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))


def is_cancel(status):
    s = (status or "").strip()
    if "반려" in s:
        return False
    return s in EXACT or any(k in s for k in KEYWORDS)


def month_back(d):
    y, m = (d.year, d.month - 1) if d.month > 1 else (d.year - 1, 12)
    for day in (d.day, 30, 29, 28):
        try:
            return date(y, m, day)
        except ValueError:
            continue


def pct(cur, prev):
    if prev == 0:
        return "신규" if cur else "-"
    return f"{(cur - prev) / prev * 100:+.1f}%"


def main():
    kst = timezone(timedelta(hours=9))
    base = (date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1
            else datetime.now(kst).date() - timedelta(days=1))
    cmp = {"전일": base - timedelta(days=1),
           "전주": base - timedelta(days=7),
           "전월": month_back(base)}
    days = {base.isoformat(): "기준일"} | {v.isoformat(): k for k, v in cmp.items()}

    meta = get("meta.json")
    rows = []
    for f in FILES:
        rows += get(f + ".json")
    months = {d[:7] for d in days}
    for ym in get("naver-index.json"):
        if ym in months:
            rows += get(f"naver/{ym}.json")

    # agg[(brand, channel)][day] = [매출, 수량, 주문번호set]
    agg = defaultdict(lambda: defaultdict(lambda: [0, 0, set()]))
    cancel = defaultdict(lambda: [0, 0])  # day -> [제외 건수, 제외 금액]
    for r in rows:
        d = str(r.get("주문일", ""))[:10]
        if d not in days:
            continue
        amt = r.get("판매금액") or 0
        if is_cancel(r.get("주문상태")):
            cancel[d][0] += 1
            cancel[d][1] += amt
            continue
        brand = r.get("브랜드") or "센티카"
        ch = r.get("채널") or "(미지정)"
        for key in ((brand, ch), (brand, "합계")):
            a = agg[key][d]
            a[0] += amt
            a[1] += r.get("수량") or 0
            a[2].add(r.get("주문번호"))

    b = base.isoformat()
    print(f"# 일일 매출 비교 — 기준일 {b} ({'월화수목금토일'[base.weekday()]})")
    print("비교일: " + ", ".join(f"{k} {v.isoformat()}" for k, v in cmp.items()))
    print("수집시각(UTC): " + json.dumps(meta, ensure_ascii=False))
    print()
    for brand in sorted({k[0] for k in agg}):
        chans = sorted((k[1] for k in agg if k[0] == brand and k[1] != "합계"),
                       key=lambda c: -agg[(brand, c)][b][0])
        print(f"## {brand}")
        print("| 채널 | 기준일 매출(원) | 수량(개) | 주문(건) | 전일 매출 | 전일비 | 전주 매출 | 전주비 | 전월 매출 | 전월비 |")
        print("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
        for ch in chans + ["합계"]:
            a = agg[(brand, ch)]
            cur = a[b]
            cells = [f"**{ch}**" if ch == "합계" else ch,
                     f"{cur[0]:,}", f"{cur[1]:,}", f"{len(cur[2]):,}"]
            for k, v in cmp.items():
                p = a[v.isoformat()][0]
                cells += [f"{p:,}", pct(cur[0], p)]
            print("| " + " | ".join(cells) + " |")
        print()
    print("취소성 제외: " + ", ".join(
        f"{days[d]} {d} {cancel[d][0]}건/{cancel[d][1]:,}원" for d in days))


if __name__ == "__main__":
    main()
