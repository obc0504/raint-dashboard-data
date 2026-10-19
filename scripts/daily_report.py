#!/usr/bin/env python3
"""브랜드별 전일 기준 일일 매출 비교 리포트 (전일 / 전주 동요일 / 전월 동일자).

사용: python3 -I scripts/daily_report.py [YYYY-MM-DD] [--brand 에이센트]
  날짜가 없으면 KST 기준 어제, 브랜드가 없으면 센티카.
  기준일이 일요일이면(월요일 발송분) 금~일 3일을 하나로 묶어
  직전 3일(화~목) / 전주 금~일 / 전월 동일자 3일과 비교하고,
  1:1 비교는 전주 금~일, 요일별 표(각 요일 vs 전주 같은 요일)를 추가.
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
  - 시간대별 매출(주문 시각이 있는 채널만, 표 제목에 채널 표시. 무신사는 주문번호의 시각): 기준일 vs 최근 28일
  - 취소성 주문 제외(정확일치 CANCELED/CANCEL/RETURNED/RETURN/EXCHANGED,
    또는 취소/반품/교환/환불/결제오류 포함), 단 '반려' 포함 시 유지
"""
import http.client, json, sys, time, urllib.error, urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

BASE = "https://raw.githubusercontent.com/obc0504/raint-dashboard-data/main/"
BRAND = "센티카"
FILES = ["cafe24", "wconcept", "29cm", "musinsa", "kakao", "qoo10"]
# 2025년 과거 데이터 (CLAUDE.md 참고 — 현재 파일과 중복 제거된 센티카 전용 추출분).
# kakao가 92MB라 기간이 2025년에 걸칠 때만 받는다.
ARCHIVE_FILES = ["archive-2025/" + f for f in ["naver", "cafe24", "29cm", "musinsa", "qoo10", "kakao"]]
ARCHIVE_END = "2025-12-31"
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
        except urllib.error.HTTPError as e:
            if e.code == 404 or i == tries - 1:  # 없는 파일은 재시도하지 않음
                raise
            time.sleep(2 ** (i + 1))
        except (OSError, http.client.HTTPException, ValueError):
            if i == tries - 1:
                raise
            time.sleep(2 ** (i + 1))


def get_optional(path):
    """저장소에 없을 수도 있는 파일: 404면 빈 목록."""
    try:
        return get(path)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return []
        raise


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


# 시간대 분석: 주문 시각이 있는 채널만 가능 (네이버 주문일시, 자사몰 결제완료일시,
# 카톡선물·W컨셉·큐텐은 임포트가 주문일시를 저장한 행부터 — W컨셉은 원본 결제일자)
HOUR_FIELDS = {"네이버": "주문일시", "자사몰": "결제완료일시", "무신사": "주문번호",
               "카톡선물": "주문일시", "W컨셉": "주문일시", "큐텐": "주문일시"}
TIME_BLOCKS = [(0, 6), (6, 9), (9, 12), (12, 15), (15, 18), (18, 21), (21, 24)]


def hour_of(r):
    if r.get("채널") == "무신사":
        # 시각 컬럼이 없지만 주문번호가 YYYYMMDDHHMMSS + 일련번호 4자리
        # (2026-10-19 확인: 1,998행 전부 날짜가 주문일과 일치)
        o = str(r.get("주문번호") or "")
        ok = (len(o) == 18 and o.isdigit() and int(o[8:10]) < 24
              and o[:8] == str(r.get("주문일", ""))[:10].replace("-", ""))
        return int(o[8:10]) if ok else None
    f = HOUR_FIELDS.get(r.get("채널"))
    t = str(r.get(f) or "") if f else ""
    return int(t[11:13]) if len(t) >= 13 and t[11:13].isdigit() else None


def hourly_table(rows, ranges, title):
    """ranges = [(라벨, 시작일, 끝일)] — 첫 번째가 기준. 시간대별 매출/주문/비중."""
    agg = {lab: [[0, set()] for _ in range(24)] for lab, _, _ in ranges}
    used = set()  # 실제로 시각이 있었던 채널 (표 제목에 표시)
    # 채널별로 판매가 있던 날 / 그중 시각이 있던 날 — 임포트가 시각을 저장하기
    # 시작한 지 얼마 안 된 채널은 비교 기간 일부만 들어가므로 표 아래에 표시
    sold_days = {lab: defaultdict(set) for lab, _, _ in ranges}
    timed_days = {lab: defaultdict(set) for lab, _, _ in ranges}
    for r in rows:
        if (r.get("브랜드") or "센티카") != BRAND or is_cancel(r.get("주문상태")):
            continue
        d = str(r.get("주문일", ""))[:10]
        ch = r.get("채널")
        h = hour_of(r)
        if ch in HOUR_FIELDS:
            for lab, s, e in ranges:
                if s.isoformat() <= d <= e.isoformat():
                    sold_days[lab][ch].add(d)
                    if h is not None:
                        timed_days[lab][ch].add(d)
        if h is None:
            continue
        for lab, s, e in ranges:
            if s.isoformat() <= d <= e.isoformat():
                agg[lab][h][0] += r.get("판매금액") or 0
                agg[lab][h][1].add(r.get("주문번호"))
                used.add(r.get("채널"))
    chs = "·".join(c for c in HOUR_FIELDS if c in used) or "해당 채널 없음"
    print(f"### {title} — {chs}만 (다른 채널은 주문 시각 데이터 없음)")
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
        part = [f"{ch} {len(timed_days[lab][ch])}/{len(sold_days[lab][ch])}일"
                for ch in HOUR_FIELDS
                if timed_days[lab][ch] and len(timed_days[lab][ch]) < len(sold_days[lab][ch])]
        if part:
            print(f"- ⚠️ {lab}: 시각 데이터가 일부 날짜만 있음({', '.join(part)} — 시각 있는 날/판매일) "
                  f"→ 이 채널이 포함된 비중은 다른 기간과 바로 비교하지 말 것")
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
    WD = "월화수목금토일"
    # 기준일이 일요일이면 월요일 발송분: 금~일 3일을 하나의 기준 기간으로 집계
    weekend = base.weekday() == 6
    start = base - timedelta(days=2) if weekend else base
    span = (base - start).days + 1
    if weekend:
        periods = {"기준": (start, base),
                   "직전3일": (start - timedelta(days=3), start - timedelta(days=1)),
                   "전주": (start - timedelta(days=7), base - timedelta(days=7)),
                   "전월": (month_back(start), month_back(base))}
        one = "전주"  # 1:1 비교 대상: 요일이 같은 전주 금~일
    else:
        periods = {"기준": (base, base),
                   "전일": (base - timedelta(days=1), base - timedelta(days=1)),
                   "전주": (base - timedelta(days=7), base - timedelta(days=7)),
                   "전월": (month_back(base), month_back(base))}
        one = "전일"
    cmp = {k: v for k, v in periods.items() if k != "기준"}

    def fmt(p):
        s_, e_ = p
        if s_ == e_:
            return f"{s_.isoformat()}({WD[s_.weekday()]})"
        return f"{s_.isoformat()}({WD[s_.weekday()]})~{e_.isoformat()}({WD[e_.weekday()]})"

    # 날짜 -> 속한 기간 라벨 (금~일 모드의 요일별 표를 위해 기준 기간 각 날짜와 D-7도 따로)
    day_labels = defaultdict(list)
    for lab, (s_, e_) in periods.items():
        for i in range((e_ - s_).days + 1):
            day_labels[(s_ + timedelta(days=i)).isoformat()].append(lab)
    if weekend:
        for i in range(span):
            d = start + timedelta(days=i)
            day_labels[d.isoformat()].append(d.isoformat())
            day_labels[(d - timedelta(days=7)).isoformat()].append((d - timedelta(days=7)).isoformat())

    meta = get("meta.json")
    rows = []
    for f in FILES:
        rows += get(f + ".json")
    if min(min(day_labels), (base - timedelta(days=27)).isoformat()) <= ARCHIVE_END:
        for f in ARCHIVE_FILES:  # 2025년에 걸치는 기간만
            rows += get_optional(f + ".json")
    months = {d[:7] for d in day_labels} | {(base - timedelta(days=27)).isoformat()[:7]}
    for ym in get("naver-index.json"):
        if ym in months:
            rows += get(f"naver/{ym}.json")

    # agg[channel][label] = [매출, 수량, 주문번호set]
    agg = defaultdict(lambda: defaultdict(lambda: [0, 0, set()]))
    cancel = defaultdict(lambda: [0, 0])  # label -> [제외 건수, 제외 금액]
    prod = defaultdict(lambda: defaultdict(lambda: [0, 0]))  # (채널, 상품)[label]
    for r in rows:
        d = str(r.get("주문일", ""))[:10]
        if d not in day_labels or (r.get("브랜드") or "센티카") != BRAND:
            continue
        amt = r.get("판매금액") or 0
        labs = day_labels[d]
        if is_cancel(r.get("주문상태")):
            for lab in labs:
                cancel[lab][0] += 1
                cancel[lab][1] += amt
            continue
        ch = r.get("채널") or "(미지정)"
        name = r.get("상품명") or "(미지정)"
        for lab in labs:
            for key in (ch, "합계"):
                a = agg[key][lab]
                a[0] += amt
                a[1] += r.get("수량") or 0
                a[2].add(r.get("주문번호"))
            pr = prod[(ch, PRODUCT_ALIAS.get(name, name))][lab]
            pr[0] += amt
            pr[1] += r.get("수량") or 0

    b = "기준"
    title = "금·토·일 매출 비교 (월요일 발송분)" if weekend else "일일 매출 비교"
    print(f"# {BRAND} {title} — 기준 {fmt(periods[b])}")
    print("비교: " + ", ".join(f"{k} {fmt(v)}" for k, v in cmp.items()))
    print("수집시각(UTC): " + json.dumps(meta, ensure_ascii=False))
    # 기준 마지막 날 다음날(KST) 이후에 수집되지 않은 파일의 채널은 미수집으로 표시
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
        def tot(lab):
            return sum(agg[c][lab][0] for c in ok)
        print(f"⚠️ 데이터 미수집 의심: {', '.join(stale)} (수집시각이 기준일 이전) — 해석에서 제외")
        print(f"미수집 채널 제외 합계: 기준 {tot(b):,} / " + " / ".join(
            f"{k} {tot(k):,} ({pct(tot(b), tot(k))})" for k in cmp))
    print()
    chans = sorted((c for c in agg if c != "합계"), key=lambda c: -agg[c][b][0])
    print("| 채널 | 기준 매출(원) | 수량(개) | 주문(건) | "
          + " | ".join(f"{k} 매출 | {k}비" for k in cmp) + " |")
    print("|---|---:|---:|---:|" + "---:|---:|" * len(cmp))
    for ch in chans + ["합계"]:
        a = agg[ch]
        cur = a[b]
        cells = [f"**{ch}**" if ch == "합계" else ch,
                 f"{cur[0]:,}", f"{cur[1]:,}", f"{len(cur[2]):,}"]
        for k in cmp:
            p = a[k][0]
            cells += [f"{p:,}", pct(cur[0], p)]
        print("| " + " | ".join(cells) + " |")
    print()
    # 1:1 비교 (매출 / 수량 / 주문): 평일은 전일, 금~일은 전주 금~일
    print(f"### {one} 1:1 비교 ({fmt(periods[b])} vs {fmt(periods[one])})")
    print(f"| 채널 | 매출 기준 | 매출 {one} | 매출 증감 | 수량 기준 | 수량 {one} | 수량 증감 "
          f"| 주문 기준 | 주문 {one} | 주문 증감 |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for ch in chans + ["합계"]:
        cur, prv = agg[ch][b], agg[ch][one]
        co, po = len(cur[2]), len(prv[2])
        cells = [f"**{ch}**" if ch == "합계" else ch,
                 f"{cur[0]:,}", f"{prv[0]:,}", pct(cur[0], prv[0]),
                 f"{cur[1]:,}", f"{prv[1]:,}", f"{cur[1] - prv[1]:+,} ({pct(cur[1], prv[1])})",
                 f"{co:,}", f"{po:,}", f"{co - po:+,} ({pct(co, po)})"]
        print("| " + " | ".join(cells) + " |")
    print()
    if weekend:  # 요일별 (각 요일 vs 전주 같은 요일)
        ds = [start + timedelta(days=i) for i in range(span)]
        print("### 요일별 합계 (vs 전주 같은 요일)")
        print("| 요일 | 매출(원) | 수량(개) | 주문(건) | 전주 매출 | 전주비 | 전주 수량 | 전주 주문 |")
        print("|---|---:|---:|---:|---:|---:|---:|---:|")
        for d in ds:
            cur, prv = agg["합계"][d.isoformat()], agg["합계"][(d - timedelta(days=7)).isoformat()]
            print(f"| {d.month}/{d.day}({WD[d.weekday()]}) | {cur[0]:,} | {cur[1]:,} | {len(cur[2]):,} "
                  f"| {prv[0]:,} | {pct(cur[0], prv[0])} | {prv[1]:,} | {len(prv[2]):,} |")
        print()
        print("### 요일별 채널 매출(원)")
        print("| 채널 | " + " | ".join(f"{d.month}/{d.day}({WD[d.weekday()]}) | 전주비" for d in ds) + " |")
        print("|---|" + "---:|---:|" * len(ds))
        for ch in chans + ["합계"]:
            cells = [f"**{ch}**" if ch == "합계" else ch]
            for d in ds:
                cur, prv = agg[ch][d.isoformat()][0], agg[ch][(d - timedelta(days=7)).isoformat()][0]
                cells += [f"{cur:,}", pct(cur, prv)]
            print("| " + " | ".join(cells) + " |")
        print()

    w = "전주"
    keys = list(periods)

    def product_table(ch, title, rising):
        items = [(n, v) for (c, n), v in prod.items() if c == ch]
        items.sort(key=lambda kv: kv[1][b][0] - kv[1][w][0], reverse=rising)
        if rising:  # 상승 채널은 전주 대비 증가 상품 상위만
            items = [kv for kv in items if kv[1][b][0] - kv[1][w][0] > 0][:RISE_TOP]
        else:  # 하락 채널은 전주 대비 감소 상품 상위만
            items = [kv for kv in items if kv[1][b][0] - kv[1][w][0] < 0][:DROP_TOP]
        print(f"### {ch} 상품별 ({title}) — 매출(원)(수량)")
        print("| 상품 | " + " | ".join(keys) + " | 전주 대비 증감(원) | "
              + " | ".join(f"단가 {k}" for k in keys) + " | 최근단가 전월비 |")
        print("|---|" + "---:|" * (2 * len(keys) + 2))
        for n, v in items:
            cells = [n] + [f"{v[x][0]:,}({v[x][1]})" for x in keys]
            cells.append(f"{v[b][0] - v[w][0]:+,}")
            # 단가 = 판매금액 합 / 수량 합 (할인 반영 실판매가)
            # 최근단가 = 기준 단가, 기준 판매 없으면 직전(전일/직전3일) 단가
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
        chg = {k: (cur - a[k][0]) / a[k][0] for k in ("전주", "전월") if a[k][0]}
        drops = [k for k, c in chg.items() if c <= -0.10]
        if drops:
            product_table(ch, f"{'/'.join(drops)}비 -10% 이하", rising=False)
        elif chg.get("전주", 0) > 0:
            product_table(ch, f"전주비 상승, 증가 상위 {RISE_TOP}개", rising=True)
    lab = "금토일" if weekend else "기준일"
    hourly_table(rows, [(lab, start, base), ("최근28일", base - timedelta(days=27), base)],
                 f"시간대별 매출 ({lab} vs 최근 28일 {(base - timedelta(days=27)).isoformat()}~{base.isoformat()})")
    print(f"취소성 제외({BRAND}): " + ", ".join(
        f"{k} {fmt(v)} {cancel[k][0]}건/{cancel[k][1]:,}원" for k, v in periods.items()))

if __name__ == "__main__":
    main()
