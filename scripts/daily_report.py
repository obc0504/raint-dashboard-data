#!/usr/bin/env python3
"""센티카 전일 기준 일일 매출 비교 리포트 (전일 / 전주 동요일 / 전월 동일자).

사용: python3 -I scripts/daily_report.py [YYYY-MM-DD]
  인자 없으면 KST 기준 어제를 기준일로 사용.
집계 규칙은 README의 세션 기준과 동일:
  - 브랜드 없으면 '센티카', 센티카만 집계
    (gmarket_auction/coupang_wing/ohouse/coupang_rocket은 100% 에이센트라 받지 않음)
  - 매출 = 판매금액 합 (품목 행 단위)
  - 상품명 변경 이력은 PRODUCT_ALIAS로 합쳐서 집계
  - 주력 채널 중 전주비 또는 전월비 -10% 이하인 채널은 상품별 표를 추가 출력
  - 취소성 주문 제외(정확일치 CANCELED/CANCEL/RETURNED/RETURN/EXCHANGED,
    또는 취소/반품/교환/환불/결제오류 포함), 단 '반려' 포함 시 유지
"""
import http.client, json, sys, time, urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

BASE = "https://raw.githubusercontent.com/obc0504/raint-dashboard-data/main/"
BRAND = "센티카"
FILES = ["cafe24", "wconcept", "29cm", "musinsa", "kakao", "qoo10"]
EXACT = {"CANCELED", "CANCEL", "RETURNED", "RETURN", "EXCHANGED"}
KEYWORDS = ["취소", "반품", "교환", "환불", "결제오류"]
MAIN_CHANNELS = ["네이버", "자사몰", "29CM", "무신사", "카톡선물", "큐텐"]
# 옛 상품명 -> 현재 상품명 (같은 상품, 이름만 변경)
PRODUCT_ALIAS = {
    "버블 핸드워시 500ml 2개입 세트": "퍼퓸 핸드솝 500ml 2개입 세트",
}


def get(path, tries=4):
    for i in range(tries):
        try:
            with urllib.request.urlopen(BASE + path, timeout=120) as r:
                return json.loads(r.read().decode("utf-8"))
        except (OSError, http.client.HTTPException, ValueError):
            if i == tries - 1:
                raise
            time.sleep(2 ** (i + 1))


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

    # agg[channel][day] = [매출, 수량, 주문번호set]
    agg = defaultdict(lambda: defaultdict(lambda: [0, 0, set()]))
    cancel = defaultdict(lambda: [0, 0])  # day -> [제외 건수, 제외 금액]
    prod = defaultdict(lambda: defaultdict(lambda: [0, 0]))  # (채널, 상품)[day]
    for r in rows:
        d = str(r.get("주문일", ""))[:10]
        if d not in days or (r.get("브랜드") or "센티카") != BRAND:
            continue
        amt = r.get("판매금액") or 0
        if is_cancel(r.get("주문상태")):
            cancel[d][0] += 1
            cancel[d][1] += amt
            continue
        ch = r.get("채널") or "(미지정)"
        for key in (ch, "합계"):
            a = agg[key][d]
            a[0] += amt
            a[1] += r.get("수량") or 0
            a[2].add(r.get("주문번호"))
        name = r.get("상품명") or "(미지정)"
        pr = prod[(ch, PRODUCT_ALIAS.get(name, name))][d]
        pr[0] += amt
        pr[1] += r.get("수량") or 0

    b = base.isoformat()
    print(f"# {BRAND} 일일 매출 비교 — 기준일 {b} ({'월화수목금토일'[base.weekday()]})")
    print("비교일: " + ", ".join(f"{k} {v.isoformat()}" for k, v in cmp.items()))
    print("수집시각(UTC): " + json.dumps(meta, ensure_ascii=False))
    print()
    chans = sorted((c for c in agg if c != "합계"), key=lambda c: -agg[c][b][0])
    print("| 채널 | 기준일 매출(원) | 수량(개) | 주문(건) | 전일 매출 | 전일비 | 전주 매출 | 전주비 | 전월 매출 | 전월비 |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for ch in chans + ["합계"]:
        a = agg[ch]
        cur = a[b]
        cells = [f"**{ch}**" if ch == "합계" else ch,
                 f"{cur[0]:,}", f"{cur[1]:,}", f"{len(cur[2]):,}"]
        for k, v in cmp.items():
            p = a[v.isoformat()][0]
            cells += [f"{p:,}", pct(cur[0], p)]
        print("| " + " | ".join(cells) + " |")
    print()
    for ch in MAIN_CHANNELS:
        a = agg.get(ch)
        if not a:
            continue
        cur = a[b][0]
        drops = [k for k in ("전주", "전월")
                 if a[cmp[k].isoformat()][0] and
                 (cur - a[cmp[k].isoformat()][0]) / a[cmp[k].isoformat()][0] <= -0.10]
        if not drops:
            continue
        print(f"### {ch} 상품별 ({'/'.join(drops)}비 -10% 이하) — 매출(원)(수량)")
        print("| 상품 | 기준일 | 전일 | 전주 | 전월 | 전주 대비 증감(원) |")
        print("|---|---:|---:|---:|---:|---:|")
        items = [(n, v) for (c, n), v in prod.items() if c == ch]
        w = cmp["전주"].isoformat()
        for n, v in sorted(items, key=lambda kv: kv[1][b][0] - kv[1][w][0]):
            cells = [n] + [f"{v[x][0]:,}({v[x][1]})"
                           for x in [b] + [d.isoformat() for d in cmp.values()]]
            cells.append(f"{v[b][0] - v[w][0]:+,}")
            print("| " + " | ".join(cells) + " |")
        print()
    print("취소성 제외(센티카): " + ", ".join(
        f"{days[d]} {d} {cancel[d][0]}건/{cancel[d][1]:,}원" for d in days))


if __name__ == "__main__":
    main()
