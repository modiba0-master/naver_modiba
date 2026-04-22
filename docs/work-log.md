# Work Log
<!-- last_commit: d4fb03b -->

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
