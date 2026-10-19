#!/usr/bin/env python3
"""카톡선물하기 주문 엑셀(어드민 다운로드)을 kakao.json과 같은 형식의 JSON으로 변환.

사용: python3 -I scripts/convert_kakao_xlsx.py 주문.xlsx 출력.json
  - 판매금액 = 상품금액 + 옵션금액 + 추가상품금액 - 판매자할인금액 - 판매자쿠폰할인금액 (= 정산기준금액)
  - 주문일은 날짜(YYYY-MM-DD)만, 주문일시는 원본 주문일 그대로(시각 포함), _pid는 주문번호
  - 개인정보 컬럼은 원본에도 없고 결과에도 넣지 않음
"""
import json, sys
import openpyxl


def n(x):
    return int(x or 0)


def main(src, dst):
    ws = openpyxl.load_workbook(src, read_only=True).worksheets[0]
    it = ws.iter_rows(values_only=True)
    head = list(next(it))
    out = []
    for vals in it:
        r = dict(zip(head, vals))
        if not r.get("주문번호"):
            continue
        out.append({
            "주문일": str(r["주문일"])[:10],
            "주문일시": str(r["주문일"]),
            "채널": "카톡선물",
            "브랜드": r.get("브랜드") or "센티카",
            "상품명": r["상품명"],
            "상품번호": str(r["채널상품번호"]),
            "수량": n(r["수량"]),
            "판매금액": n(r["상품금액"]) + n(r["옵션금액"]) + n(r["추가상품금액"])
                        - n(r["판매자할인금액"]) - n(r["판매자쿠폰할인금액"]),
            "주문번호": str(r["주문번호"]),
            "주문상태": r["주문상태"],
            "옵션정보": r.get("옵션") or "",
            "최초배송비번호": str(r.get("최초배송비번호") or ""),
            "_pid": str(r["주문번호"]),
        })
    out.sort(key=lambda r: (r["주문일"], r["주문번호"]))
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print(f"{len(out)}행 → {dst} ({out[0]['주문일']} ~ {out[-1]['주문일']})")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
