#!/usr/bin/env python3
"""주간 / 월간 매출 비교 리포트 (일일 리포트와 같은 집계 규칙).

사용:
  python3 -I scripts/period_report.py week [YYYY-MM-DD]   # 해당 날짜가 속한 주의 직전 완료 주(월~일)
  python3 -I scripts/period_report.py month [YYYY-MM]     # 지정 월 (없으면 KST 기준 지난달)
  python3 -I scripts/period_report.py month-ready         # 지난달 월간 리포트를 만들 데이터가 다 들어왔는지
  python3 -I scripts/period_report.py status [YYYY-MM-DD] # 오늘 보낼 리포트(일간/주간/월간)와 데이터 준비 여부
  옵션: --brand 에이센트 (없으면 센티카)
주간 리포트는 두 부분:
  A. 금·토·일 (평일 일일 리포트가 없는 3일) — 3일 합계와 요일별, 전주 금·토·일과 비교
  B. 한 주 전체(월~일) — 전주, 최근 4주 평균과 비교
월간 리포트: 지난달 vs 그 전달 (월 합계와 하루 평균)
집계 규칙:
  - 브랜드 없으면 '센티카'로 간주하고 지정 브랜드만 집계
  - 취소성 주문 제외(정확일치 CANCELED/CANCEL/RETURNED/RETURN/EXCHANGED,
    또는 취소/반품/교환/환불/결제오류 포함), 단 '반려' 포함 시 유지
  - 매출 = 판매금액 합 (품목 행 단위), 주문 = 고유 주문번호 수
  - 상품명 변경 이력은 PRODUCT_ALIAS로 합쳐서 집계
  - 상품별 단가 = 판매금액 합 / 수량 합 (할인 반영 실판매가)
  - 수집시각이 기간 마지막 날 이전인 채널은 미수집 경고와 제외 합계를 출력
  - 시간대별 매출(네이버·자사몰만, 주문 시각이 있는 채널)
"""
import calendar, http.client, json, sys, time, urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

BASE = "https://raw.githubusercontent.com/obc0504/raint-dashboard-data/main/"
KST = timezone(timedelta(hours=9))
BRAND = "센티카"
FILES = ["cafe24", "wconcept", "29cm", "musinsa", "kakao", "qoo10"]
AICENT_FILES = ["gmarket_auction", "coupang_wing", "ohouse", "coupang_rocket"]
EXACT = {"CANCELED", "CANCEL", "RETURNED", "RETURN", "EXCHANGED"}
KEYWORDS = ["취소", "반품", "교환", "환불", "결제오류"]
MAIN_CHANNELS = ["네이버", "자사몰", "29CM", "무신사", "카톡선물", "큐텐"]
AICENT_MAIN = ["네이버", "쿠팡윙", "자사몰", "쿠팡로켓", "카톡선물", "오늘의집", "지마켓", "옥션"]
RISE_TOP = 5
DROP_TOP = 10
FILE_CHANNELS = {"naver": ["네이버"], "cafe24": ["자사몰"], "wconcept": ["W컨셉"],
                 "29cm": ["29CM"], "musinsa": ["무신사"], "kakao": ["카톡선물"],
                 "qoo10": ["큐텐"], "gmarket_auction": ["지마켓", "옥션"],
                 "coupang_wing": ["쿠팡윙"], "ohouse": ["오늘의집"],
                 "coupang_rocket": ["쿠팡로켓"]}
PRODUCT_ALIAS = {
    "버블 핸드워시 500ml 2개입 세트": "퍼퓸 핸드솝 500ml 2개입 세트",
    "[선물포장/카드] 센티카 버블 핸드워시 500ml 2개입 세트":
        "[선물포장/카드] 센티카 버블 핸드워시 500ml 2개입 세트 선물세트/집들이선물",
}
WD = "월화수목금토일"
# 자동 발송 시작점: 이보다 이전 기간의 주간/월간은 status에서 SKIP
FIRST_WEEK_END = date(2026, 10, 18)
FIRST_MONTH = (2026, 10)


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


def pct(cur, prev):
    if prev == 0:
        return "신규" if cur else "-"
    return f"{(cur - prev) / prev * 100:+.1f}%"


def num(x):
    """정수는 그대로, 평균처럼 소수가 생기면 소수 둘째 자리까지 표기."""
    if float(x).is_integer():
        return f"{int(x):,}"
    return f"{x:,.2f}"


def ds(d):
    return f"{d.month}/{d.day}({WD[d.weekday()]})"


def span(s, e):
    return f"{ds(s)}~{ds(e)}"


def days_between(s, e):
    return [s + timedelta(days=i) for i in range((e - s).days + 1)]


class Data:
    """기간(label -> (시작, 끝)) 별로 채널/상품 집계."""

    def __init__(self, periods):
        self.periods = periods
        self.meta = get("meta.json")
        lo = min(s for s, _ in periods.values())
        hi = max(e for _, e in periods.values())
        rows = []
        for f in FILES:
            rows += get(f + ".json")
        months = {(lo + timedelta(days=i)).strftime("%Y-%m") for i in range((hi - lo).days + 1)}
        for ym in get("naver-index.json"):
            if ym in months:
                rows += get(f"naver/{ym}.json")
        self.rows = rows  # 시간대 분석용
        # agg[채널][label] = [매출, 수량, 주문번호set] / prod[(채널, 상품)][label] = [매출, 수량]
        self.agg = defaultdict(lambda: defaultdict(lambda: [0, 0, set()]))
        self.prod = defaultdict(lambda: defaultdict(lambda: [0, 0]))
        self.cancel = defaultdict(lambda: [0, 0])
        bounds = {k: (s.isoformat(), e.isoformat()) for k, (s, e) in periods.items()}
        for r in rows:
            d = str(r.get("주문일", ""))[:10]
            if (r.get("브랜드") or "센티카") != BRAND or not (lo.isoformat() <= d <= hi.isoformat()):
                continue
            labels = [k for k, (s, e) in bounds.items() if s <= d <= e]
            if not labels:
                continue
            amt = r.get("판매금액") or 0
            qty = r.get("수량") or 0
            if is_cancel(r.get("주문상태")):
                for k in labels:
                    self.cancel[k][0] += 1
                    self.cancel[k][1] += amt
                continue
            ch = r.get("채널") or "(미지정)"
            name = r.get("상품명") or "(미지정)"
            name = PRODUCT_ALIAS.get(name, name)
            for k in labels:
                for key in (ch, "합계"):
                    a = self.agg[key][k]
                    a[0] += amt
                    a[1] += qty
                    a[2].add(r.get("주문번호"))
                p = self.prod[(ch, name)][k]
                p[0] += amt
                p[1] += qty

    def channels(self, label):
        return sorted((c for c in self.agg if c != "합계"), key=lambda c: -self.agg[c][label][0])

    def stale(self, end):
        out = []
        for f, chs in FILE_CHANNELS.items():
            t = self.meta.get(f)
            if not t:
                continue
            got = datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(KST).date()
            if got <= end:
                out += [c for c in chs if c in self.agg]
        return out

    def val(self, ch, label, div=1):
        a = self.agg[ch][label]
        return a[0] / div, a[1] / div, len(a[2]) / div


def print_stale(data, end, main, comps):
    st = data.stale(end)
    print("수집시각(UTC): " + json.dumps(data.meta, ensure_ascii=False))
    if not st:
        return
    ok = [c for c in data.agg if c != "합계" and c not in st]
    tot = lambda k, div=1: sum(data.agg[c][k][0] for c in ok) / div
    print(f"⚠️ 데이터 미수집 의심: {', '.join(st)} (수집시각이 기간 마지막 날 이전) — 해석에서 제외")
    print(f"미수집 채널 제외 합계: {main} {num(tot(main))} / " + " / ".join(
        f"{k} {num(tot(k, dv))} ({pct(tot(main), tot(k, dv))})" for k, dv in comps))


def channel_table(data, main, comps, title):
    """채널별 매출 + 비교 기간 매출/증감. comps = [(label, 나눗수)]."""
    print(f"### {title}")
    head = "| 채널 | 매출(원) | 수량(개) | 주문(건) | " + " | ".join(f"{k} 매출 | {k}비" for k, _ in comps) + " |"
    print(head)
    print("|---|" + "---:|" * (3 + 2 * len(comps)))
    for ch in data.channels(main) + ["합계"]:
        m = data.val(ch, main)
        cells = [f"**{ch}**" if ch == "합계" else ch, num(m[0]), num(m[1]), num(m[2])]
        for k, dv in comps:
            p = data.val(ch, k, dv)
            cells += [num(p[0]), pct(m[0], p[0])]
        print("| " + " | ".join(cells) + " |")
    print()


def one_to_one(data, main, comp, title, div=1):
    print(f"### {title}")
    print(f"| 채널 | 매출 {main} | 매출 {comp} | 매출 증감 | 수량 {main} | 수량 {comp} | 수량 증감 "
          f"| 주문 {main} | 주문 {comp} | 주문 증감 |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for ch in data.channels(main) + ["합계"]:
        m, p = data.val(ch, main), data.val(ch, comp, div)
        cells = [f"**{ch}**" if ch == "합계" else ch,
                 num(m[0]), num(p[0]), pct(m[0], p[0]),
                 num(m[1]), num(p[1]), f"{m[1] - p[1]:+,.0f} ({pct(m[1], p[1])})" if div == 1
                 else f"{num(m[1] - p[1])} ({pct(m[1], p[1])})",
                 num(m[2]), num(p[2]), f"{m[2] - p[2]:+,.0f} ({pct(m[2], p[2])})" if div == 1
                 else f"{num(m[2] - p[2])} ({pct(m[2], p[2])})"]
        print("| " + " | ".join(cells) + " |")
    print()


def product_tables(data, main, comp, comp_name):
    """주력 채널별: 비교 기간 대비 -10% 이하면 감소 상위, 올랐으면 증가 상위 상품."""
    for ch in MAIN_CHANNELS:
        if ch not in data.agg:
            continue
        cur, prv = data.agg[ch][main][0], data.agg[ch][comp][0]
        if prv and (cur - prv) / prv <= -0.10:
            rising, title = False, f"{comp_name}비 {pct(cur, prv)}, 감소 상위 {DROP_TOP}개"
        elif cur > prv:
            rising, title = True, f"{comp_name}비 {pct(cur, prv)}, 증가 상위 {RISE_TOP}개"
        else:
            continue
        items = [(n, v) for (c, n), v in data.prod.items() if c == ch]
        diff = lambda kv: kv[1][main][0] - kv[1][comp][0]
        items.sort(key=diff, reverse=rising)
        items = [kv for kv in items if (diff(kv) > 0 if rising else diff(kv) < 0)]
        items = items[:RISE_TOP if rising else DROP_TOP]
        print(f"#### {ch} 상품별 ({title}) — 매출(원)(수량)")
        print(f"| 상품 | {main} | {comp_name} | 증감(원) | 단가 {main} | 단가 {comp_name} | 단가 변화 |")
        print("|---|---:|---:|---:|---:|---:|---:|")
        for n, v in items:
            m, p = v[main], v[comp]
            um = m[0] / m[1] if m[1] else None
            up = p[0] / p[1] if p[1] else None
            cells = [n, f"{m[0]:,}({m[1]})", f"{p[0]:,}({p[1]})", f"{m[0] - p[0]:+,}",
                     f"{um:,.0f}" if um is not None else "-",
                     f"{up:,.0f}" if up is not None else "-",
                     f"{(um - up) / up * 100:+.1f}%" if um is not None and up else "-"]
            print("| " + " | ".join(cells) + " |")
        print()


def last_completed_week(ref):
    """ref 날짜 기준 직전에 끝난 월~일 주."""
    end = ref - timedelta(days=ref.weekday() + 1)  # 직전 일요일
    return end - timedelta(days=6), end


def week_report(ref):
    s, e = last_completed_week(ref)
    fri = e - timedelta(days=2)
    ps, pe = s - timedelta(days=7), e - timedelta(days=7)
    periods = {
        "이번주": (s, e), "전주": (ps, pe), "4주평균": (s - timedelta(days=28), e - timedelta(days=7)),
        "금토일": (fri, e), "전주 금토일": (fri - timedelta(days=7), e - timedelta(days=7)),
    }
    for d in days_between(s, e):
        periods[ds(d)] = (d, d)
        periods[f"전주 {ds(d)}"] = (d - timedelta(days=7), d - timedelta(days=7))
    data = Data(periods)
    print(f"# {BRAND} 주간 매출 리포트 — {span(s, e)}")
    print(f"비교: 전주 {span(ps, pe)}, 4주평균 {span(s - timedelta(days=28), e - timedelta(days=7))} 합계 ÷ 4")
    print_stale(data, e, "이번주", [("전주", 1), ("4주평균", 4)])
    print()

    print(f"## A. 금·토·일 ({span(fri, e)}) — 평일 일일 리포트가 없는 3일")
    channel_table(data, "금토일", [("전주 금토일", 1)], f"A-1. 금·토·일 3일 합계 (vs 전주 금·토·일 {span(fri - timedelta(days=7), pe)})")
    one_to_one(data, "금토일", "전주 금토일", "A-2. 금·토·일 1:1 비교 (매출/수량/주문)")
    print("### A-3. 요일별 (합계)")
    print("| 요일 | 매출(원) | 수량(개) | 주문(건) | 전주 같은 요일 매출 | 전주비 | 전주 수량 | 전주 주문 |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|")
    for d in days_between(fri, e):
        m, p = data.val("합계", ds(d)), data.val("합계", f"전주 {ds(d)}")
        print(f"| {ds(d)} | {num(m[0])} | {num(m[1])} | {num(m[2])} | {num(p[0])} | {pct(m[0], p[0])} | {num(p[1])} | {num(p[2])} |")
    print()
    print("### A-4. 요일별 채널 매출(원)")
    fds = days_between(fri, e)
    print("| 채널 | " + " | ".join(f"{ds(d)} | 전주비" for d in fds) + " |")
    print("|---|" + "---:|" * (2 * len(fds)))
    for ch in data.channels("금토일") + ["합계"]:
        cells = [f"**{ch}**" if ch == "합계" else ch]
        for d in fds:
            m, p = data.val(ch, ds(d))[0], data.val(ch, f"전주 {ds(d)}")[0]
            cells += [num(m), pct(m, p)]
        print("| " + " | ".join(cells) + " |")
    print()
    print("### A-5. 금·토·일 상품별 (vs 전주 금·토·일)")
    product_tables(data, "금토일", "전주 금토일", "전주")

    print(f"## B. 한 주 전체 ({span(s, e)})")
    channel_table(data, "이번주", [("전주", 1), ("4주평균", 4)], "B-1. 채널별 주간 실적 (4주평균은 4주 합계 ÷ 4)")
    one_to_one(data, "이번주", "전주", "B-2. 전주 1:1 비교 (매출/수량/주문)")
    print("### B-3. 일자별 흐름 (합계)")
    print("| 일자 | 매출(원) | 수량(개) | 주문(건) | 전주 같은 요일 매출 | 전주비 |")
    print("|---|---:|---:|---:|---:|---:|")
    for d in days_between(s, e):
        m, p = data.val("합계", ds(d)), data.val("합계", f"전주 {ds(d)}")
        print(f"| {ds(d)} | {num(m[0])} | {num(m[1])} | {num(m[2])} | {num(p[0])} | {pct(m[0], p[0])} |")
    print()
    print("### B-4. 주간 상품별 (vs 전주)")
    product_tables(data, "이번주", "전주", "전주")
    hourly_table(data.rows, [("이번주", s, e), ("전주", ps, pe), ("금토일", fri, e)],
                 "B-5. 시간대별 매출 (이번주 / 전주 / 금토일)")
    print("취소성 제외: " + ", ".join(f"{k} {data.cancel[k][0]}건/{data.cancel[k][1]:,}원"
                                 for k in ("이번주", "전주", "금토일", "전주 금토일")))


def month_range(y, m):
    return date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])


def prev_month(y, m):
    return (y, m - 1) if m > 1 else (y - 1, 12)


def month_report(y, m):
    s, e = month_range(y, m)
    py, pm = prev_month(y, m)
    ps, pe = month_range(py, pm)
    n, pn = (e - s).days + 1, (pe - ps).days + 1
    data = Data({"이번달": (s, e), "전월": (ps, pe)})
    print(f"# {BRAND} 월간 매출 리포트 — {y}년 {m}월 ({s.isoformat()} ~ {e.isoformat()}, {n}일)")
    print(f"비교: 전월 {py}년 {pm}월 ({pn}일). 하루 평균 = 월 합계 ÷ 일수")
    print_stale(data, e, "이번달", [("전월", 1)])
    print()
    channel_table(data, "이번달", [("전월", 1)], "1. 채널별 월간 실적")
    print("### 2. 하루 평균 비교 (월마다 일수가 달라서)")
    print(f"| 채널 | {m}월 하루 평균 매출 | {pm}월 하루 평균 매출 | 증감 | {m}월 하루 평균 수량 | {pm}월 하루 평균 수량 | {m}월 하루 평균 주문 | {pm}월 하루 평균 주문 |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|")
    for ch in data.channels("이번달") + ["합계"]:
        a, b = data.val(ch, "이번달", n), data.val(ch, "전월", pn)
        print(f"| {'**' + ch + '**' if ch == '합계' else ch} | {num(a[0])} | {num(b[0])} | {pct(a[0], b[0])} "
              f"| {num(a[1])} | {num(b[1])} | {num(a[2])} | {num(b[2])} |")
    print()
    one_to_one(data, "이번달", "전월", "3. 전월 1:1 비교 (월 합계, 매출/수량/주문)")
    print("### 4. 월간 상품별 (vs 전월)")
    product_tables(data, "이번달", "전월", "전월")
    hourly_table(data.rows, [("이번달", s, e), ("전월", ps, pe)], "5. 시간대별 매출 (이번달 / 전월)")
    print("취소성 제외: " + ", ".join(f"{k} {data.cancel[k][0]}건/{data.cancel[k][1]:,}원" for k in ("이번달", "전월")))


def month_ready(today):
    """지난달 마지막 날 데이터가 모든 채널 파일에 들어왔으면 READY."""
    y, m = prev_month(today.year, today.month)
    _, e = month_range(y, m)
    meta = get("meta.json")
    files = FILES + ["naver"]
    late = []
    for f in files:
        t = meta.get(f)
        got = datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(KST).date() if t else None
        if got is None or got <= e:
            late.append(f"{f}({got})")
    print(f"{y}-{m:02d} READY" if not late else f"{y}-{m:02d} NOT_READY: " + ", ".join(late))


def collected(meta):
    """센티카 파일별 수집 날짜(KST) 중 가장 늦지 않은(가장 이른) 날짜."""
    out = []
    for f in FILES + ["naver"]:
        t = meta.get(f)
        out.append(datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(KST).date() if t else date.min)
    return min(out)


def status(today):
    """오늘(평일) 처리할 리포트와 준비 여부. 수집 날짜 > 대상 기간 마지막 날이면 READY."""
    got = collected(get("meta.json"))
    y = today - timedelta(days=1)
    if 1 <= today.weekday() <= 4:
        print(f"DAILY {y.isoformat()} {'READY' if got > y else 'NOT_READY'} "
              f"subject=[센티카 일일 리포트] {y.month:02d}/{y.day:02d}({WD[y.weekday()]})")
    else:
        print("DAILY SKIP (월요일은 주간 리포트로 대체, 주말은 발송 안 함)")
    s, e = last_completed_week(today)
    wst = "SKIP(자동 발송 시작 전)" if e < FIRST_WEEK_END else ("READY" if got > e else "NOT_READY")
    print(f"WEEK {s.isoformat()}~{e.isoformat()} {wst} "
          f"subject=[센티카 주간 리포트] {s.month:02d}/{s.day:02d}~{e.month:02d}/{e.day:02d}")
    py, pm = prev_month(today.year, today.month)
    _, me = month_range(py, pm)
    mst = "SKIP(자동 발송 시작 전)" if (py, pm) < FIRST_MONTH else ("READY" if got > me else "NOT_READY")
    print(f"MONTH {py}-{pm:02d} {mst} "
          f"subject=[센티카 월간 리포트] {py}-{pm:02d}")
    print(f"(센티카 데이터 수집 날짜 KST: {got.isoformat()})")


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
    today = datetime.now(KST).date()
    mode = args[0] if args else "week"
    if mode == "week":
        week_report(date.fromisoformat(args[1]) if len(args) > 1 else today)
    elif mode == "month":
        if len(args) > 1:
            y, m = map(int, args[1].split("-"))
        else:
            y, m = prev_month(today.year, today.month)
        month_report(y, m)
    elif mode == "status":
        status(date.fromisoformat(args[1]) if len(args) > 1 else today)
    elif mode == "month-ready":
        month_ready(date.fromisoformat(args[1]) if len(args) > 1 else today)
    else:
        sys.exit(f"알 수 없는 모드: {mode}")


if __name__ == "__main__":
    main()
