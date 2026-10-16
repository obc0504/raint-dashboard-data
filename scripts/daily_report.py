#!/usr/bin/env python3
"""브랜드별 전일 기준 일일 매출 비교 리포트 (전일 / 전주 동요일 / 전월 동일자).

사용: python3 -I scripts/daily_report.py [YYYY-MM-DD] [--brand 에이센트]
  날짜가 없으면 KST 기준 어제, 브랜드가 없으면 센티카.
집계 규칙은 README의 세션 기준과 동일:
  - 브랜드 없으면 '센티카'로 간주하고 지정 브랜드만 집계
    (gmarket_auction/coupang_wing/ohouse/coupang_rocket은 100% 에이센트라
     센티카 리포트에서는 받지 않음)
  - 매출 = 판매금액 합 (품목 행 단위)
  - 채널 표 다음에 전일 1:1 비교 표(매출/수량/주문) 출력
  - 상품명 변경 이력은 PRODUCT_ALIAS로 합쳐서 집계
  - 주력 채널 중 전주비 또는 전월비 -10% 이하인 채널은 상품별 표를 추가 출력
    (상품별 단가 = 판매금액 합 / 수량 합, 할인 반영 실판매가)
  - 하락이 없고 전주비가 오른 주력 채널은 증가 상위 상품 표를 출력
  - 하락 채널 상품 표는 전주 대비 감소 상위 10개만
  - 수집시각이 기준일 이전인 채널은 미수집 경고와 제외 합계를 출력
  - 시간대별 매출(네이버·자사몰만, 주문 시각이 있는 채널): 기준일 vs 최근 28일
  - 취소성 주문 제외(정확일치 CANCELED/CANCEL/RETURNED/RETURN/EXCHANGED,
    또는 취소/반품/교환/환불/결제오류 포함), 단 '반려' 포함 시 유지
"""
import http.client, json, sys, time, urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

BASE = "https://raw.githubusercontent.com/obc0504/raint-dashboard-data/main/"
BRAND = "센티카"
FILES = ["cafe24", "wconcept", "29cm", "musinsa", "kakao", "qoo10"]
AICENT_FILES = ["gmarket_auction", "coupang_wing", "ohouse", "coupang_rocket"]
EXACT = {"CANCELED", "CANCEL", "RETURNED", "RETURN", "EXCHANGED"}
KEYWORDS = ["취소", "반품", "교환", "환불", "결제오류"]
MAIN_CHANNELS = ["네이버", "자사몰", "29CM", "무신사", "카톡선물", "큐텐"]
AICENT_MAIN = ["네이버", "쿠팡윙", "자사몰", "쿠팡로켓", "카톡선물", "오늘의집", "지마켓", "옥션"]
RISE_TOP = 5
DROP_TOP = 10
# 데이터 파일 -> 채널명 (수집시각 확인용)
FILE_CHANNELS = {"naver": ["네이버"], "cafe24": ["자사몰"], "wconcept": ["W컨셉"],
                 "29cm": ["29CM"], "musinsa": ["무신사"], "kakao": ["카톡선물"],
                 "qoo10": ["큐텐"], "gmarket_auction": ["지마켓", "옥션"],
                 "coupang_wing": ["쿠팡윙"], "ohouse": ["오늘의집"],
                 "coupang_rocket": ["쿠팡로켓"]}
# 옛 상품명 -> 현재 상품명 (같은 상품, 이름만 변경)
PRODUCT_ALIAS = {
    "버블 핸드워시 500ml 2개입 세트": "퍼퓸 핸드솝 500ml 2개입 세트",
    "[선물포장/카드] 센티카 버블 핸드워시 500ml 2개입 세트":
        "[선물포장/카드] 센티카 버블 핸드워시 500ml 2개입 세트 선물세트/집들이선물",
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


# 시간대 분석: 주문 시각이 있는 채널(네이버 주문일시, 자사몰 결제완료일시)만 가능
HOUR_FIELDS = {"네이버": "주문일시", "자사몰": "결제완료일시"}
TIME_BLOCKS = [(0, 6), (6, 9), (9, 12), (12, 15), (15, 18), (18, 21), (21, 24)]


def hour_of(r):
    f = HOUR_FIELDS.get(r.get("채널"))
    t = str(r.get(f) or "") if f else ""
    return int(t[11:13]) if len(t) >= 13 and t[11:13].isdigit() else None


def hourly_table(rows, ranges, title):
    """ranges = [(라벨, 시작일, 끝일)] — 첫 번째가 기준. 시간대별 매출/주문/비중."""
    agg = {lab: [[0, set()] for _ in range(24)] for lab, _, _ in ranges}
    for r in rows:
        if (r.get("브랜드") or "센티카") != BRAND or is_cancel(r.get("주문상태")):
            continue
        h = hour_of(r)
        if h is None:
            continue
        d = str(r.get("주문일", ""))[:10]
        for lab, s, e in ranges:
            if s.isoformat() <= d <= e.isoformat():
                agg[lab][h][0] += r.get("판매금액") or 0
                agg[lab][h][1].add(r.get("주문번호"))
    print(f"### {title} — 네이버·자사몰만 (다른 채널은 주문 시각 데이터 없음)")
    head = "| 시간대 | " + " | ".join(f"{lab} 매출(원) | {lab} 주문 | {lab} 비중" for lab, _, _ in ranges) + " |"
    print(head)
    print("|---|" + "---:|" * (3 * len(ranges)))
    tot = {lab: sum(x[0] for x in agg[lab]) for lab, _, _ in ranges}
    for a, b in TIME_BLOCKS:
        cells = [f"{a:02d}~{b:02d}시"]
        for lab, _, _ in ranges:
            v = sum(agg[lab][h][0] for h in range(a, b))
            o = len(set().union(*(agg[lab][h][1] for h in range(a, b))))
            cells += [f"{v:,}", f"{o:,}", f"{v / tot[lab] * 100:.1f}%" if tot[lab] else "-"]
        print("| " + " | ".join(cells) + " |")
    cells = ["**합계**"]
    for lab, _, _ in ranges:
        cells += [f"**{tot[lab]:,}**", f"**{len(set().union(*(x[1] for x in agg[lab]))):,}**", "100%" if tot[lab] else "-"]
    print("| " + " | ".join(cells) + " |")
    for lab, _, _ in ranges:
        if tot[lab]:
            top = sorted(range(24), key=lambda h: -agg[lab][h][0])[:3]
            print(f"- {lab} 매출 상위 시간: " + ", ".join(f"{h}시 {agg[lab][h][0]:,}원" for h in top))
    print()


def main():
    global BRAND, FILES, MAIN_CHANNELS
    args = sys.argv[1:]
    if "--brand" in args:
        i = args.index("--brand")
        BRAND = args[i + 1]
        del args[i:i + 2]
    if BRAND != "센티카":
        FILES = FILES + AICENT_FILES
    if BRAND == "에이센트":
        MAIN_CHANNELS = AICENT_MAIN
    kst = timezone(timedelta(hours=9))
    base = (date.fromisoformat(args[0]) if args
            else datetime.now(kst).date() - timedelta(days=1))
    cmp = {"전일": base - timedelta(days=1),
           "전주": base - timedelta(days=7),
           "전월": month_back(base)}
    days = {base.isoformat(): "기준일"} | {v.isoformat(): k for k, v in cmp.items()}

    meta = get("meta.json")
    rows = []
    for f in FILES:
        rows += get(f + ".json")
    months = {d[:7] for d in days} | {(base - timedelta(days=27)).isoformat()[:7]}
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
    # 기준일 다음날(KST) 이후에 수집되지 않은 파일의 채널은 미수집으로 표시
    stale = []
    for f, chs in FILE_CHANNELS.items():
        t = meta.get(f)
        if not t:
            continue
        got = datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(kst).date()
        if got <= base:
            stale += [c for c in chs if c in agg]
    if stale:
        ok = [c for c in agg if c != "합계" and c not in stale]
        def tot(d):
            return sum(agg[c][d][0] for c in ok)
        print(f"⚠️ 데이터 미수집 의심: {', '.join(stale)} (수집시각이 기준일 이전) — 해석에서 제외")
        print(f"미수집 채널 제외 합계: 기준일 {tot(b):,} / " + " / ".join(
            f"{k} {tot(v.isoformat()):,} ({pct(tot(b), tot(v.isoformat()))})" for k, v in cmp.items()))
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
    # 전일 1:1 비교 (매출 / 수량 / 주문)
    y = cmp["전일"].isoformat()
    print(f"### 전일 1:1 비교 ({b} vs {y})")
    print("| 채널 | 매출 기준일 | 매출 전일 | 매출 증감 | 수량 기준일 | 수량 전일 | 수량 증감 "
          "| 주문 기준일 | 주문 전일 | 주문 증감 |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for ch in chans + ["합계"]:
        cur, prv = agg[ch][b], agg[ch][y]
        co, po = len(cur[2]), len(prv[2])
        cells = [f"**{ch}**" if ch == "합계" else ch,
                 f"{cur[0]:,}", f"{prv[0]:,}", pct(cur[0], prv[0]),
                 f"{cur[1]:,}", f"{prv[1]:,}", f"{cur[1] - prv[1]:+,} ({pct(cur[1], prv[1])})",
                 f"{co:,}", f"{po:,}", f"{co - po:+,} ({pct(co, po)})"]
        print("| " + " | ".join(cells) + " |")
    print()

    w = cmp["전주"].isoformat()
    keys = [b] + [d.isoformat() for d in cmp.values()]

    def product_table(ch, title, rising):
        items = [(n, v) for (c, n), v in prod.items() if c == ch]
        items.sort(key=lambda kv: kv[1][b][0] - kv[1][w][0], reverse=rising)
        if rising:  # 상승 채널은 전주 대비 증가 상품 상위만
            items = [kv for kv in items if kv[1][b][0] - kv[1][w][0] > 0][:RISE_TOP]
        else:  # 하락 채널은 전주 대비 감소 상품 상위만
            items = [kv for kv in items if kv[1][b][0] - kv[1][w][0] < 0][:DROP_TOP]
        print(f"### {ch} 상품별 ({title}) — 매출(원)(수량)")
        print("| 상품 | 기준일 | 전일 | 전주 | 전월 | 전주 대비 증감(원) "
              "| 단가 기준일 | 단가 전일 | 단가 전주 | 단가 전월 | 최근단가 전월비 |")
        print("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
        for n, v in items:
            cells = [n] + [f"{v[x][0]:,}({v[x][1]})" for x in keys]
            cells.append(f"{v[b][0] - v[w][0]:+,}")
            # 단가 = 판매금액 합 / 수량 합 (할인 반영 실판매가)
            # 최근단가 = 기준일 단가, 기준일 판매 없으면 전일 단가
            up = [v[x][0] / v[x][1] if v[x][1] else None for x in keys]
            cells += [f"{u:,.0f}" if u is not None else "-" for u in up]
            recent = up[0] if up[0] is not None else up[1]
            cells.append(f"{(recent - up[3]) / up[3] * 100:+.1f}%"
                         if recent is not None and up[3] else "-")
            print("| " + " | ".join(cells) + " |")
        print()

    for ch in MAIN_CHANNELS:
        a = agg.get(ch)
        if not a:
            continue
        cur = a[b][0]
        chg = {k: (cur - a[cmp[k].isoformat()][0]) / a[cmp[k].isoformat()][0]
               for k in ("전주", "전월") if a[cmp[k].isoformat()][0]}
        drops = [k for k, c in chg.items() if c <= -0.10]
        if drops:
            product_table(ch, f"{'/'.join(drops)}비 -10% 이하", rising=False)
        elif chg.get("전주", 0) > 0:
            product_table(ch, f"전주비 상승, 증가 상위 {RISE_TOP}개", rising=True)
    hourly_table(rows, [("기준일", base, base), ("최근28일", base - timedelta(days=27), base)],
                 f"시간대별 매출 (기준일 vs 최근 28일 {(base - timedelta(days=27)).isoformat()}~{b})")
    print(f"취소성 제외({BRAND}): " + ", ".join(
        f"{days[d]} {d} {cancel[d][0]}건/{cancel[d][1]:,}원" for d in days))


if __name__ == "__main__":
    main()
