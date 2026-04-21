# naver_modiba — 에이전트용 요약

## 목적
네이버 스마트스토어 주문·분석 데이터를 백엔드에 두고, Streamlit 대시보드로 조회·집계한다.

## 디렉터리
| 경로 | 역할 |
|------|------|
| `app/` | FastAPI, 라우터(`routers/`), 모델, `services/`(동기화·네이버·분석) |
| `streamlit_app/` | Streamlit UI — `app.py`, `dashboard.py`, `services/`(쿼리·DB·그리드) |
| `dashboard.py` (루트) | 별도 진입/레거시 가능 — `streamlit_app`과 중복 시 동시 수정 검토 |
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
- **`load_dotenv()`**: `app/config.py` 최상단 + `streamlit_app/services/db.py`에서 프로젝트 루트 `sys.path` 후 로드.
- **`app/db_url_utils.py`**: 비밀번호 URL 파싱 실패 시 `quote_plus` 재인코딩, `print_database_url_diagnostics` (호스트·`public_host`·마스킹 URL). **`pytest`가 로드된 경우에는 print 생략**.
- **로컬에서 `*.railway.internal` 연결 불가** 시: `.env`에 `DATABASE_URL_USE_PUBLIC=1` + Railway **`DATABASE_PUBLIC_URL`** 설정.

### 테스트
- `tests/test_order_transformer.py`, `tests/test_services.py`, `tests/test_analytics_api.py` 등으로 상기 규칙 검증.

## 최신 작업 상태 (2026-04-17)
- 아키텍처 전환:
  - API 요청 경로에서 네이버 직접 호출 제거
  - 백엔드 스케줄러가 1분 주기(`order_poll_interval_seconds=60`)로 `sync_orders` + `generate_daily_summary` 수행
  - `/analytics/*`는 DB 조회 전용으로 유지
- 안정화:
  - `daily_summary` 미존재 시 자동 생성 fallback 추가
  - 스케줄러 락을 thread lock으로 전환(요청 루프 블로킹 방지)
- 대시보드:
  - KPI/분석 필터 분리, KPI 일자 테이블(합계 포함), 옵션 환산수량/팩수량/중량단위 표시
  - 기본 새로고침은 캐시 유지, 강제 새로고침 버튼 분리
  - API 실패 시 `session_state`의 직전 성공 데이터 fallback 표시
  - 컬럼명 한글화(`weight_unit`, `pack_count`, `pack_count_sum`, `converted_quantity`)
- 배포 메모:
  - Railway 서비스는 `naver_modiba`(백엔드) / `naver_modiba_dashboard`(대시보드) 분리 운영
  - 메인 API 도메인은 `https://navermodiba-production.up.railway.app`를 기준으로 사용
  - fallback 502 재발 시 서비스-도메인 매핑과 런타임 로그를 먼저 확인

## 세션 마무리 메모 (2026-04-17, Request ID: VfSdsATdSJ22qr2aacI7Nw)
- 상태 확인 결과:
  - `https://navermodiba-production.up.railway.app/health`는 502 응답 확인
  - `https://navermodibadashboard-production-5e93.up.railway.app/`는 200 응답 확인
- 해석:
  - 대시보드 앱 자체보다는 백엔드 메인 도메인/서비스(`naver_modiba`) 쪽 장애 가능성이 높음
  - 현재 구조상 대시보드 새로고침/데이터 조회는 네이버 API 직접 호출이 아니라 DB 조회 API 경로를 사용
- 즉시 이어서 할 일:
  1. 로컬/작업 터미널에서 `railway login` 재인증
  2. `naver_modiba` 최신 배포 로그 확인(크래시/포트 바인딩/환경변수 누락 여부)
  3. 필요 시 `naver_modiba` 재배포/재시작
  4. `GET /health`, `GET /analytics/orders-raw` 200 복구 확인
