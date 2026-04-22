# naver_modiba — 에이전트용 요약

## 목적
네이버 스마트스토어 주문·분석 데이터를 백엔드에 두고, Streamlit 대시보드로 조회·집계한다.

## 디렉터리
| 경로 | 역할 |
|------|------|
| `app/` | FastAPI, 라우터(`routers/`), 모델, `services/`(동기화·네이버·분석) |
| `streamlit_app/` | Streamlit UI — `dashboard.py`(실구현), `run.py`(배포 엔트리), `services/`(쿼리·DB·그리드) |
| `dashboard.py` (루트) | 레거시 호환용 포워더(실구현 없음) — `streamlit_app/dashboard.py`로 위임 |
| `tests/` | API·서비스 테스트 |
| `naver_commerce_proxy/` | 프록시/별도 서비스 모듈 |

## 대시보드 실행 시 확인
- 실제로 어떤 파일이 Procfile·Railway·문서에서 실행되는지 확인한 뒤 그 경로를 기준으로 편집한다.

## 환경
- 네이버 API: 클라이언트 자격·판매자 ID 등은 `app/config.py` 및 배포 시크릿 참고.
- 배포에서 네이버 호출이 403 `GW.IP_NOT_ALLOWED`이면 코드 문제가 아니라 **허용 IP** 이슈일 수 있다.

## Cursor
- 상세 규칙: `.cursor/rules/*.mdc`
- 대시보드 작업 워크플로: `.cursor/skills/naver-modiba-dashboard/SKILL.md`

## 에이전트 연속용 컨텍스트 (2026-04-22) — 다음 호출 시 유지할 것

### 데이터 모델 (주문·매출일)
- **`payment_date`**: 네이버 결제 **원본 시각** (16시 영업일 로직 없음). `app/services/sync.py`의 `parse_payment_datetime_string`.
- **파싱 규칙**: `Z`/`z` 접미사 → 접미사 제거 후 naive 파싱 → **`+9시간`** (UTC 벽시각 → KST naive). 그 외 naive는 KST로 간주, aware는 `to_kst_naive`.
- **`business_date` / `payment_business_date`**: `payment_date`에 **16:00 영업일 규칙** (`app/services/order_transformer.py` → `hour >= 16`이면 익일 `date()`). DB에 저장.
- **파생 경로**: `app/services/naver_orders_sync.py` (`calculate_business_date`, `to_kst_naive`) + `sync.py`에서 insert 시 `row["business_date"] = calculate_business_date(row["payment_date"])` 패턴.

### API·분석
- **`GET /analytics/orders-raw`**: 기간 있을 때 SQL에서 **`business_date`(및 revenue_basis에 맞는 coalesce 컬럼)** 로 필터 — 전량 로드 후 Python 필터 아님. 라우터: `app/routers/analytics.py`, 로직: `app/services/analytics_service.py`.

### 스크립트
- **`scripts/recompute_business_dates.py`**: MySQL/SQLite/PG에 맞춰 **`business_date`·`payment_business_date` 벌크 UPDATE** (16시 CASE) 후 Python 배치로 주문·발송 영업일·`net_revenue` 정리. `--no-bulk-sql` / `--verify-only` 지원.

### 설정·로컬 DB (Railway)
- **FastAPI `app/config.py`**: `load_dotenv` (optional), `app/db_url_utils.py`로 URL 정규화·시작 시 진단 print (`pytest` 제외).
- **Streamlit** (`streamlit_app`만 배포): **`app` 패키지 없음** — `streamlit_app/services/db_url.py`의 `get_streamlit_database_url()`로 `DATABASE_URL` / `DATABASE_URL_USE_PUBLIC` + `DATABASE_PUBLIC_URL` / 비밀번호 인코딩을 **API와 동일 규칙**으로 처리. `services/db.py`는 `app.config`를 import 하지 않는다.
- **로컬에서 `*.railway.internal` 연결 불가** 시: `DATABASE_URL_USE_PUBLIC=1` + `DATABASE_PUBLIC_URL`.

### 테스트
- `tests/test_order_transformer.py`, `tests/test_services.py`, `tests/test_analytics_api.py` 등으로 상기 규칙 검증.

## 최신 작업 상태 (2026-04-22)
- 동기화/정확도:
  - `PAYED_DATETIME` 리스트 응답 축약 이슈 대응: `product-orders/query` 상세 재조회로 필드 보강 후 저장.
  - query 호출을 청크(300) + 429/5xx 재시도로 안정화(`app/services/naver.py`).
  - `paymentDate` 누락 행은 `orderDate -> placeOrderDate -> sendDate` fallback 저장으로 누락 방지.
  - 중복 정책: 상품주문번호 최초 1회 저장, 이후 동일 번호는 기본 무시. 교환/반품/취소만 갱신.
- DB/원장:
  - `orders`에 네이버 주문원장 확장 컬럼 다수 추가(배송비/수수료/옵션코드/결제위치 등).
  - 기존 주문도 재동기화 시 비어있는 확장 컬럼 자동 보강.
  - 신규 조회 API 추가: `GET /analytics/orders-ledger` (운영/다운로드용 상세 원장), `GET /analytics/orders-claims`.
- 상태/운영 가시성:
  - `GET /health`에 DB probe, orders count, latest payment date, scheduled job 상태 포함.
  - `GET /analytics/db-stats`에 `latest_business_date` 포함.
- lookback 운영:
  - 평시 고정 구간(`NAVER_COMMERCE_ORDER_LOOKBACK_HOURS`, 기본 24h).
  - 수동 누락복구 전용 `NAVER_BACKFILL_LOOKBACK_HOURS`(기본 0, 필요 시만 활성화).
- 대시보드:
  - 기본 API URL을 `https://web-production-0001b.up.railway.app`로 조정.
  - API/캐시 모두 실패 시 빈 화면 대신 오류 메시지 표시.
  - 실제 접속 URL: `https://navermodibadashboard-production-5e93.up.railway.app`.
- 배포/검증:
  - 최신 배포에서 `inserted_count` 증가(누락분 반영)와 `orders_count` 상승 확인.
  - 최근 확인값 예시: `orders_count=633`, `latest_payment_date=2026-04-22T17:13:42`, `latest_business_date=2026-04-23`.

## 참고 규칙 추가
- `.cursor/rules/naver-commerce-api-reference.mdc` 추가.
- 네이버 API 작업 시 기술지원 레포와 공식 문서 우선 참고:
  - https://github.com/commerce-api-naver/commerce-api
  - https://apicenter.commerce.naver.com/ko/basic/commerce-api
