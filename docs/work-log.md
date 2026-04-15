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
