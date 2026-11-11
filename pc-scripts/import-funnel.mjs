/* ============================================================
   전환율(유입) 엑셀 → 데이터 저장소 funnel/ 폴더로 복사 후 커밋·푸시
   (수동 채널, JSON 병합 없음 — 원본 엑셀을 그대로 보관한다)

   다운로드 폴더에서 아래 2종을 모두 찾는다:
   1) 네이버 방문분석(상품별): 이름이 visit_ 로 시작, 컬럼 날짜/채널상품번호/방문수
      → funnel/네이버_방문분석_센티카_시작일_종료일.xlsx  (날짜 컬럼의 최소~최대일)
   2) 카톡선물하기 방문구매전환현황(상품): 컬럼 상품명/기간/주문건수 (+유입수)
      → funnel/시작일_종료일_방문구매전환현황_상품.xlsx  (유입수 없으면 _상품별_주문자수_유입수없음)
   같은 이름이 이미 있으면 건너뛴다(덮어쓰지 않음). 파일이 없어도 에러 없이 끝난다.
   실행: npm run import:funnel   (npm run import:all 마지막에 자동 실행)
   ============================================================ */

import { existsSync, copyFileSync, mkdirSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { listDownloadCandidates, readXlsxRows, toDateStr } from './lib/import-common.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const DATA_REPO = process.env.FUNNEL_DATA_REPO || resolve(HERE, '..', '..', 'raint-dashboard-data');
const OUT = resolve(DATA_REPO, 'funnel');

if (!existsSync(DATA_REPO)) {
  console.log(`\n[전환율] ${DATA_REPO} 가 없어서 건너뜁니다.\n`);
  process.exit(0);
}

const pad = (n) => String(n).padStart(2, '0');
function dateRange(values) {
  const ds = [];
  for (const v of values) {
    const s = String(v ?? '');
    let m = s.match(/(\d{4})[-.](\d{1,2})[-.](\d{1,2})/g);
    if (!m) { const t = toDateStr(v); m = t ? [t] : []; }
    for (const x of m) {
      const [y, mo, d] = x.split(/[-.]/).map(Number);
      if (y > 2000 && mo >= 1 && mo <= 12) ds.push(`${y}-${pad(mo)}-${pad(d)}`);
    }
  }
  ds.sort();
  return ds.length ? [ds[0], ds[ds.length - 1]] : null;
}
const short = (a, b) => `${a}_${b.slice(5)}`; // 2026-10-11_11-09

const copied = [];
const skipped = [];
const unreadable = [];

const seen = new Set();
const argPath = process.argv[2]; // 파일명이 다를 때: npm run import:funnel -- "경로"
const files = (argPath
  ? [{ name: argPath, path: resolve(argPath) }]
  : [...listDownloadCandidates(['visit_']), ...listDownloadCandidates(['방문구매전환', '전환현황', '주문자수', '유입'])])
  .filter((f) => !seen.has(f.path) && seen.add(f.path));

for (const f of files) {
  let rows;
  try { rows = await readXlsxRows(f.path); } catch (e) { unreadable.push(`${f.name} (${e.message})`); continue; }
  const h = new Set(Object.keys(rows[0] || {}));
  let target = null;
  if (['날짜', '채널상품번호', '방문수'].every((c) => h.has(c))) {
    // 2026-11-11부터 파일에 '날짜 기준' 컬럼(조회기간/비교기간)이 추가됨 — 비교기간 행은 제외하고 조회기간으로만 파일명을 정한다
    const own = rows.filter((x) => !('날짜 기준' in x) || String(x['날짜 기준']).trim() === '조회기간');
    const r = dateRange(own.map((x) => x['날짜']));
    if (r) target = `네이버_방문분석_센티카_${r[0]}_${r[1].slice(5)}.xlsx`;
  } else if (['상품명', '기간', '주문건수'].every((c) => h.has(c))) {
    const r = dateRange(rows.map((x) => x['기간']));
    if (r) target = h.has('유입수') ? `${short(r[0], r[1])}_방문구매전환현황_상품.xlsx` : `${short(r[0], r[1])}_상품별_주문자수_유입수없음.xlsx`;
  }
  if (!target) continue; // 전환율 파일이 아님
  const dest = resolve(OUT, target);
  if (existsSync(dest)) { skipped.push(target); continue; }
  mkdirSync(OUT, { recursive: true });
  copyFileSync(f.path, dest);
  copied.push(target);
}

console.log('\n====================================================');
console.log('전환율(유입) 파일 → 데이터 저장소 funnel/');
console.log('====================================================');
for (const t of copied) console.log(`  복사 완료  ${t}`);
for (const t of skipped) console.log(`  이미 있음  ${t}`);
for (const t of unreadable) console.log(`  읽기 실패  ${t}`);
if (!copied.length && !skipped.length) console.log('  전환율 파일을 찾지 못했습니다 (visit_*.xlsx / 방문구매전환현황). 건너뜁니다.');

if (copied.length) {
  const run = (args) => spawnSync('git', args, { cwd: DATA_REPO, stdio: 'inherit' }).status === 0;
  run(['pull', '--rebase', 'origin', 'main']);
  run(['add', 'funnel']);
  run(['commit', '-m', `전환율 파일 추가: ${copied.join(', ')}`]);
  run(['push', 'origin', 'main']);
}
