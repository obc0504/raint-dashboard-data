import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import daily_report as d
from datetime import date, timedelta
from collections import defaultdict
# 사용: python3 -I price_reco.py 시작일 종료일  (YYYY-MM-DD)
#  채널별 Top5(기준 구간 매출 기준) 상품의 "추천 판매가" 표를 출력한다.
#  분석 구간: 종료일 포함 최근 90일. 최종 구매가 = 판매금액 ÷ 수량(쿠폰 차감 후), 100원 단위 반올림.
#  일별 최빈 개당가(수량 기준)를 정하고, 판매가 없던 날은 직전 판매일 가격이 이어진 것으로 본다(첫 판매일~종료일의 달력 일수).
#  가격별 일평균 매출이 가장 높은 가격 = 추천가. 달력 5일 미만 또는 판매일 3일 미만 가격은 제외.
s, e = sys.argv[1:3]
S, E = date.fromisoformat(s), date.fromisoformat(e)
W0 = (E - timedelta(days=89)).isoformat()
NAMES = {}
rows = []
for f in d.FILES: rows += d.get(f + ".json")
for ym in d.get("naver-index.json"):
    if ym >= W0[:7] and ym <= e[:7]: rows += d.get(f"naver/{ym}.json")
CH = ["카톡선물", "네이버", "무신사", "큐텐", "29CM", "자사몰", "W컨셉"]
def key_of(r):
    ch = r.get("채널")
    name = d.PRODUCT_ALIAS.get(r.get("상품명") or "(미지정)", r.get("상품명") or "(미지정)")
    if ch == "카톡선물":
        grp = "뷰티" if str(r.get("상품번호")) in d.KAKAO_BEAUTY_IDS else "홈"
        NAMES[("카톡선물(" + grp + ")", str(r.get("상품번호")))] = (str(r.get("주문일", ""))[:10], name) if NAMES.get(("카톡선물(" + grp + ")", str(r.get("상품번호"))), ("",))[0] <= str(r.get("주문일", ""))[:10] else NAMES[("카톡선물(" + grp + ")", str(r.get("상품번호")))]
        return ("카톡선물(" + grp + ")", str(r.get("상품번호")))
    NAMES[(ch, name)] = (str(r.get("주문일", ""))[:10], name)
    return (ch, name)
cur = defaultdict(lambda: [0, 0]); data = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: [0, 0])))
for r in rows:
    if (r.get("브랜드") or "센티카") != d.BRAND or d.is_cancel(r.get("주문상태")): continue
    day = str(r.get("주문일", ""))[:10]
    amt, q = r.get("판매금액") or 0, r.get("수량") or 0
    name = d.PRODUCT_ALIAS.get(r.get("상품명") or "(미지정)", r.get("상품명") or "(미지정)")
    if name.startswith("(배송비") or amt <= 0 or q <= 0: continue
    k = key_of(r)
    if s <= day <= e: cur[k][0] += amt; cur[k][1] += q
    if W0 <= day <= e:
        u = int(round(amt / q / 100.0)) * 100
        data[k][day][u][0] += q; data[k][day][u][1] += amt
def analyze(k):
    days = data[k]
    if not days: return None
    first = min(days); last_day = date.fromisoformat(e)
    cal = []; price = None; day_i = date.fromisoformat(first)
    while day_i <= last_day:
        ds = day_i.isoformat()
        if ds in days:
            price = max(days[ds].items(), key=lambda kv: kv[1][0])[0]
            q = sum(v[0] for v in days[ds].values()); a = sum(v[1] for v in days[ds].values()); sold = 1
        else:
            q = a = sold = 0
        cal.append((ds, price, q, a, sold)); day_i += timedelta(days=1)
    g = defaultdict(lambda: [0, 0, 0, 0, None, None])
    for ds, p, q, a, sold in cal:
        x = g[p]; x[0] += 1; x[1] += q; x[2] += a; x[3] += sold
        x[4] = ds if x[4] is None else x[4]; x[5] = ds
    return cal, g
def won(v): return f"{round(v):,}"
print(f"## 채널별 메인상품 추천 판매가 — 기준 {s}~{e}, 분석 구간 {W0}~{e}(최근 90일)")
print("계산: 최종 구매가(판매금액 ÷ 수량, 쿠폰 차감 후, 100원 단위) 별로 그 가격이 이어진 기간의 일평균 매출(= 기간 매출 합 ÷ 달력 일수)을 비교해 가장 높은 가격을 추천가로 표시. 달력 5일 미만·판매일 3일 미만 가격은 제외. 행사·쿠폰·노출 변화 등 가격 외 요인은 반영하지 않은 관찰값(인과 아님).")
for ch in ["카톡선물(홈)", "카톡선물(뷰티)"] + CH[1:]:
    items = sorted(((k, v) for k, v in cur.items() if k[0] == ch), key=lambda x: -x[1][0])[:5]
    if not items: continue
    print(f"\n### {ch}")
    print("| 순위 | 상품 | 현재가(원) | 현재가 일평균 매출(원) | 추천가(원) | 추천가 일평균 매출(원) | 추천가 일평균 수량(개) | 추천가 기간(달력일/판매일) | 현재가 대비 매출 | 판단 |")
    print("|---:|---|---:|---:|---:|---:|---:|---|---:|---|")
    for rank, (k, v) in enumerate(items, 1):
        res = analyze(k)
        nm = NAMES[k][1][:40]
        if not res:
            print(f"| {rank} | {nm} | - | - | - | - | - | - | - | 90일 판매 없음 |"); continue
        cal, g = res
        # 현재가 = 기준 구간 최빈 개당가
        cnt = defaultdict(int)
        for ds, p, q, a, sold in cal:
            if s <= ds <= e and q: cnt[p] += q
        curp = max(cnt.items(), key=lambda kv: kv[1])[0] if cnt else cal[-1][1]
        cs = g.get(curp)
        cur_avg = cs[2] / cs[0] if cs else 0
        ok = {p: x for p, x in g.items() if x[0] >= 5 and x[3] >= 3}
        if not ok:
            print(f"| {rank} | {nm} | {won(curp)} | {won(cur_avg)}{'' if (cs and cs[0] >= 5 and cs[3] >= 3) else '(표본 적음)'} | - | - | - | - | - | 표본 부족(가격별 5일·판매 3일 미만) |"); continue
        bp, bx = max(ok.items(), key=lambda kv: kv[1][2] / kv[1][0])
        best_avg = bx[2] / bx[0]; best_q = bx[1] / bx[0]
        cur_ok = bool(cs and cs[0] >= 5 and cs[3] >= 3)
        ratio = (best_avg / cur_avg) if (cur_ok and cur_avg) else None
        diff = f"{(ratio - 1) * 100:+.1f}%" if ratio is not None else "-"
        if not cur_ok:
            judge = f"현재가 표본 적음({cs[0] if cs else 0}일) — 추천가는 참고"
        elif bp == curp:
            judge = "현재가 유지"
        elif ratio >= 3 and bx[0] <= 21:
            judge = "행사·이벤트 구간 가능성(가격 외 요인 확인 필요)"
        elif ratio >= 1.1:
            judge = ("가격 인하 검토" if bp < curp else "가격 인상 검토") + "(확인 필요)"
        else:
            judge = "차이 작음(10% 미만), 현재가 유지"
        cur_disp = won(cur_avg) if cur_ok else f"{won(cur_avg)}(표본 적음)"
        print(f"| {rank} | {nm} | {won(curp)} | {cur_disp} | {won(bp)} | {won(best_avg)} | {best_q:.1f} | {bx[4][5:]}~{bx[5][5:]} ({bx[0]}/{bx[3]}) | {diff} | {judge} |")
