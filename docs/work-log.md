# Work Log
<!-- last_commit: 45d45b6 -->

## 2026-04-15 Session Summary

- Railway project switched to `031_naver_modiba`.
- Verified live outbound IP from Railway runtime: `182.208.212.186`.
- Naver Commerce API connectivity restored (token + order APIs returning 200).
- Backend service URL: `https://web-production-0001b.up.railway.app`.
- Dashboard URL: `https://navermodibadashboard-production-5e93.up.railway.app`.

### Backend hardening

- Added scheduler-based auto sync in `app/main.py`:
  - APScheduler interval: every 10 minutes
  - duplicate prevention (`max_instances=1` + lock)
- Added sync endpoint protection:
  - `SYNC_API_KEY` env var in config
  - `/analytics/sync-orders` requires `x-sync-key` when configured
  - verified unauthorized call returns 401
- Data preservation patch:
  - removed startup table drop behavior from `app/main.py`
  - startup now only runs `Base.metadata.create_all`
- Added payload guardrail in `app/services/sync.py`:
  - skips rows with invalid quantity/amount

### Aggregation / schema

- Added `app/services/daily_summary_service.py` with `generate_daily_summary()`.
- Added helper script: `scripts/apply_daily_summary_table.py`.
- Created/verified `daily_summary` table in Railway MariaDB.
- Executed summary generation successfully:
  - `scanned_orders=19`, `upserted_rows=15`, `batches=1`

### Dashboard updates

- New Streamlit structure:
  - `streamlit_app/app.py`
  - `streamlit_app/services/db.py`
  - `streamlit_app/services/queries.py`
- Updated dashboard queries to use business-day sales logic:
  - business day based on `business_date` (16:00 cutoff logic already computed by backend)
  - focused statuses: `신규주문`, `배송준비`
  - Friday 16:00+ orders effectively counted in Monday via `business_date`
- Updated Streamlit Procfile to run `app.py`.

## Next Call Quick Start

1. Check backend health:
   - `GET https://web-production-0001b.up.railway.app/health`
2. Check protected sync endpoint:
   - without `x-sync-key` => should be 401
   - with `x-sync-key` => should be 200
3. Confirm scheduler logs on Railway (`web` service) include:
   - `APScheduler started: /analytics/sync-orders every 10 minutes`
4. Validate dashboard pages:
   - Main/Product/Option/Time sections
   - status filter and business-day behavior in displayed metrics

## 2026-04-16 Session Summary

- Unified table rendering to `streamlit_app/services/data_grid.py` so `st.dataframe` is centralized.
- Restricted header localization to UI-only (display copy) and kept API/DB schema keys unchanged.
- Added API response column normalization in dashboards:
  - `camelCase`/`kebab-case` -> `snake_case`
  - alias mapping (`orderer_name`->`buyer_name`, `shipping_address`->`address`, etc.)
  - fallback `business_date` -> `date` when needed
- Updated Korean display labels and ordering for requested table headers:
  - `date` -> `날자`
  - `option_name` -> `옵션상품명`
  - `order_count` -> `주문수량`
  - `real_quantity` -> `수량집계`
  - `total_amount` -> `주문금액`
- Aligned local Streamlit entrypoint with web dashboard behavior by routing `streamlit_app/app.py` to run `dashboard.py`.
- Fixed Railway dashboard runtime errors:
  - added `httpx` to `streamlit_app/requirements.txt`
  - added `streamlit_app/column_map.py` so dashboard deployment can import column map in `/streamlit_app` root deployment mode.
- Railway checks:
  - confirmed outbound IP for web service: `182.208.212.186`
  - redeployed dashboard service successfully (`SUCCESS`) after dependency/import fixes.

## 2026-04-22 Structure Simplification

- Streamlit entrypoint 구조를 단순화:
  - `streamlit_app/dashboard.py`를 단일 구현 파일로 고정
  - `streamlit_app/run.py`는 `dashboard.py`로 위임하는 얇은 런처로 축소
  - 루트 `dashboard.py`는 레거시 호환용 포워더로 축소
- Import 경로 정리:
  - `streamlit_app/services/data_grid.py`에서 `sys.path` 조작을 제거
  - `streamlit_app.column_map` 명시 import로 통일
- 검증:
  - 수정 파일 컴파일 검사 통과
  - linter 오류 없음

## 2026-04-22 Dashboard Hardening & UI Refinement

- Dashboard-only 작업 원칙 확정:
  - `streamlit_app/**` 외 경로는 수정하지 않는 방향으로 진행.
- 배포 import 안정화:
  - `streamlit_app/services/data_grid.py`에 `streamlit_app.column_map` -> `column_map` fallback 추가.
  - Railway `/app` 루트 배포에서 `ModuleNotFoundError: streamlit_app` 대응.
- 네트워크/운영 안정성 개선:
  - API 호출 재시도(429/5xx + timeout/transport) 및 백오프 적용.
  - API 실패 시 마지막 정상 캐시 데이터 fallback + 경고 표시.
  - API 최근 성공 시각/연속 실패 횟수/최근 오류 노출.
  - `streamlit_autorefresh` 컴포넌트 로딩 실패 시 자동새로고침만 비활성화하고 앱은 계속 동작.
- UI 고도화(요청 반영):
  - 제목 변경: `네이버 친절한 모디바 주문현황`.
  - 좌측 사이드 메뉴 제거.
  - 상단 `새로고침` / `강제 새로고침` 버튼 제거.
  - KPI 기본 조회를 오늘 기준으로 설정하고, 비교는 1주 전 동일 구간으로 고정.
  - 분석 탭(상품/옵션) 컬럼 순서 정렬:
    - 이름(상품명/옵션명), 수량, 주문수량, 주문금액, 수량집계.
- 관련 커밋:
  - `f87e6f0` dashboard import fallback
  - `2703853` resilience + UI simplification
  - `d5fc66e` autorefresh component guard
  - `45d45b6` title/KPI/analysis column order update

## 2026-04-23 Top Tab Navigation 적용

- 대시보드 레이아웃을 상단 탭 방식으로 재구성:
  - `KPI`, `요약`, `고객`, `상품`, `마진`, `분석상세`
- 기존 계산/집계 기능은 유지하고, 보이는 구조만 탭 기준으로 재배치.
- 기존 `화면 보기(요약/분석)` 라디오 분기 제거, 분석 기능은 `분석상세` 탭으로 통합.
- 검증:
  - `python -m py_compile "streamlit_app/dashboard.py"` 통과
  - linter 오류 없음
- Git/배포:
  - 커밋 `4fc0343` 푸시 완료
  - Railway 배포 `6eba7a2f-c6cc-49e1-84f7-2704397fb4cc` 상태 `SUCCESS`
