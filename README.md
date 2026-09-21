# raint-dashboard-data

센티카 채널별 판매 대시보드(`raint-dashboard`)가 쓰는 **주문 데이터 전용** 저장소입니다.

## 왜 별도 저장소인가

대시보드 소스코드는 `raint-dashboard`(비공개) 저장소에 있습니다. 예전엔 데이터 파일도 그 저장소에 같이 커밋했는데, 그러면 데이터가 바뀔 때마다(=거의 매시간) Netlify가 사이트 전체를 다시 빌드·배포해서 무료 크레딧이 금방 소진됐습니다.

이 저장소는 **공개(public)** 라서, 대시보드가 `raw.githubusercontent.com`으로 이 저장소의 파일을 직접 읽어옵니다. GitHub에 푸시되는 즉시(수 분 내) 반영되고, Netlify 재배포가 전혀 필요 없습니다.

## 담고 있는 내용

- `naver.json`, `cafe24.json` — API로 자동 수집(채널별 주문 6~8컬럼 집계: 주문일/채널/상품명/수량/판매금액/주문번호/정산예정금액/주문상태)
- `wconcept.json`, `29cm.json`, `musinsa.json`, `kakao.json` — API 연동이 없는 채널. 사장님이 각 채널 관리자센터에서 받은 주문 엑셀을 `scripts/import-*.mjs`로 수동 임포트
- `meta.json` — 채널별 마지막 수집 시각 (`{ "naver": "ISO8601", ... }`), 대시보드 상단 "마지막 수집" 표시에 사용
- **개인정보 없음** — 고객명·연락처·주소 등은 절대 포함하지 않습니다.

## 누가 갱신하나

- `naver.json`, `cafe24.json`, `meta.json` — PC의 `run-daily.bat`(Windows 작업 스케줄러, 매시간)이 네이버·카페24 API에서 받은 데이터를 자동으로 커밋·푸시
- `wconcept.json`, `29cm.json`, `musinsa.json`, `kakao.json` — 사장님이 각 채널 주문 엑셀을 다운로드한 뒤 `npm run import:<채널>`을 수동으로 실행할 때마다 갱신
