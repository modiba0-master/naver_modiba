# 2026-04-21 세션 정리

## 이번 세션에서 반영한 코드 변경

- 매출 귀속일 계산을 KST 16:00 컷오프 기준으로 통일.
  - 금/주말 월요일 이월 규칙 제거.
  - 귀속일 D는 결제 시각(KST) `[D-1 16:00, D 16:00)` 구간으로 집계.
- API/대시보드 일자 기준 통일.
  - `orders-raw`, `orders-by-date`, KPI 집계를 `business_date` 중심으로 정렬.
  - 대시보드에 `aggregation_window_kst`(매출집계구간) 표시 추가.
- 주말 정산 보조 테이블/표시 로직 제거.
- DB 검증 스크립트 추가/개선.
  - `scripts/check_orders_business_date.py`
  - `scripts/sql/check_orders_business_date.sql`
  - `scripts/verify_database_url.py` 개선:
    - URL 파싱 진단 강화
    - `--show-masked-url` 추가
    - `--prefer-mariadb-public` 추가
    - `--use-local-url` 추가 (`DATABASE_URL_LOCAL` 우선)
- `.env`에 로컬 검증용 `DATABASE_URL_LOCAL` 추가.

## 운영/연결 이슈 결론

- `naver_modiba (back)` 서비스의 Railway 변수에서 현재 `DATABASE_URL`은 내부 주소(`mariadb.railway.internal`) 기준으로 확인됨.
- 로컬 PC에서 내부 주소로 테스트하면 `getaddrinfo failed`가 정상 동작.
- 로컬 검증은 public proxy(`monorail.proxy.rlwy.net`) URL 사용이 필요.
- `1045 Access denied`가 발생한 경우는 URL 형식 문제가 아니라 계정/비밀번호 불일치 이슈로 판단.

## 다음 재개 시 우선 체크

1. Railway `naver_modiba (back)`의 최신 `DATABASE_URL`(완성 문자열) 재확인.
2. 로컬 검증 시 `--use-local-url`로 public URL 경로 확인.
3. `scripts/check_orders_business_date.py --date YYYY-MM-DD`로 귀속일 데이터 점검.
4. 백엔드 로그에서 스케줄러 동작/실패 여부 확인.
