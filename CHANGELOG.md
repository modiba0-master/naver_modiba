## 2026-04-14 진행 정리 (대기 상태)

### 1) 코드/배포 반영 내역
- 실데이터 연동 전환:
  - `app/services/naver.py`를 mock 반환에서 실제 네이버 커머스 API 호출로 변경
  - OAuth2 Client Credentials + bcrypt 전자서명 + Base64 인코딩 적용
  - 토큰 URL: `/external/v1/oauth2/token`
  - 주문 조회 흐름: `last-changed-statuses` -> `product-orders/query`
- 환경변수 확장:
  - `NAVER_COMMERCE_API_CLIENT_ID/SECRET` + `NAVER_CLIENT_ID/SECRET` 별칭 지원
  - `NAVER_SELLER_ID` 설정 필드 추가
- 오류 추적 강화:
  - 인증 실패 시 상태코드/응답본문/traceId 포함 로그 반영
- 대시보드 한글화:
  - KPI/섹션/필터 라벨 한글로 변경
- Git 반영:
  - `ecc0f85`, `3ca2b37`, `d4fb03b` 커밋 생성 및 `main` 푸시 완료
- Railway 반영:
  - 백엔드/대시보드 재배포 완료

### 2) 검증 결과
- 로컬 환경:
  - 실 API 호출 성공 (`fetch_naver_orders()` 정상, 주문건 조회 확인)
  - 로컬 DB 초기화 후 동기화/필드 매칭(이름/주소/옵션) 검증 완료
- 배포(Railway) 환경:
  - `GET /health` 정상
  - `POST /analytics/sync-orders` 실패(500)
  - 원인: 네이버 게이트웨이 403 `GW.IP_NOT_ALLOWED`

### 3) 확인된 핵심 원인 및 공식 조치 가이드
- 네이버 응답 코드: `GW.IP_NOT_ALLOWED` (HTTP 403) — **커머스API 플랫폼에 도달한 HTTP 요청 기준 출발 IPv4**가, [내 스토어 애플리케이션](https://apicenter.commerce.naver.com/ko/member/application/manage/list)에 등록된 **API 호출 IP**와 다를 때 발생 ([공식 FAQ Discussion #2291](https://github.com/commerce-api-naver/commerce-api/discussions/2291))
- **등록할 값:** API를 호출하는 컴퓨터·인스턴스의 **Outbound(나가는) 공인 IPv4** (애플리케이션당 최대 3개, 공식 문서상 **IPv6 미지원**)
- **사설 IP만 있는 경우:** 로컬 사설 대역이 아니라 **NAT 등을 통한 공인 IP**를 API 호출 IP에 넣어야 함
- **클라우드(Railway 등):** 외부 HTTP 요청 시 출구 IP가 바뀌는 **유동 IP**가 흔함 → 안정적으로 쓰려면 공급자에서 **고정 공인 IP**(Public IP, Elastic IP 등 명칭은 플랫폼별 상이) 할당 여부를 확인
- **리전·대역:** 일부 클라우드는 고정 IP가 **비한국 대역**으로 잡힐 수 있어 API 이용에 불리할 수 있음 → 공식 가이드는 **대한민국 할당 대역·국내 리전** 사용을 권장(상세·KRNIC 링크는 위 Discussion 본문 참고)
- **DNS 주의:** `api.commerce.naver.com` 등에 대한 DNS 조회 결과(A 레코드)는 **내 서버의 출구 IP가 아님** → API 호출 IP로 등록 대상이 되지 않음
- **공개 커뮤니케이션:** 이슈·문서·댓글에는 **전체 IPv4를 기재하지 않음** (민감정보·공간 정책)

### 4) 수집된 traceId (기술지원 전달용)
- `zVJp5D0RTUOFgWjJl-SeMg^1775023537158^114449223`
- `xv75NqaOSH6Gw59c_c_bLA^1775029895874^115750109`
- `Wmn9RAliQVSp2s90j7XEcg^1775023537658^116436377`
- `Wq5sS3R3SPO0KEWW4W4p-g^1775029892869^116616987`

### 5) 외부 커뮤니케이션 상태
- 네이버 커머스 API 문의 등록 완료
- GitHub 기술지원 채널(commerce-api-naver/commerce-api)에도 문의 등록 완료

### 6) 재개 시 바로 할 일 (Discussion #2291 기준)
1. [커머스API센터 → 내 스토어 애플리케이션](https://apicenter.commerce.naver.com/ko/member/application/manage/list)에서 대상 앱 **API 호출 IP** 확인·수정(우측 상단 수정, 최대 3개)
2. 배포 인스턴스에서 **실제 Outbound 공인 IPv4** 확인 후, 위 항목에 등록(필요 시 **고정 egress IP** 확보 후 그 주소 등록)
3. 네이버 측 추가 회신이 있으면 반영
4. `POST /analytics/sync-orders` 재검증 → `GET /analytics/orders-raw` 건수·필드 매칭 재검증
5. 정상화 후 Railway 등 최종 배포 상태 확인
6. 막힐 때 원인 정리는 [Discussion #2291](https://github.com/commerce-api-naver/commerce-api/discussions/2291) FAQ와 대조

## 2026-04-17 진행 정리 (구조 전환 및 안정화)

### 1) 아키텍처 변경
- 목표 구조로 전환:
  - `네이버 API -> DB 저장(배치)`
  - `Streamlit -> FastAPI DB 조회 API`
- `POST /analytics/sync-orders` 제거, `/analytics/*`는 조회 전용 유지
- 스케줄러가 1분 주기로 `sync_orders` + `generate_daily_summary` 실행

### 2) 백엔드 안정화
- `daily_summary` 테이블 미존재 오류(`NoSuchTableError`) 대응:
  - `DailySummary` 모델 추가
  - 집계 서비스에서 테이블 자동 생성 fallback 추가
- 스케줄러 락을 thread lock으로 개선(요청 루프 블로킹 완화)
- 수동 부트스트랩 스크립트(`scripts/apply_daily_summary_table.py`)에 `DATABASE_URL` fallback 추가

### 3) 대시보드 기능/UX 반영
- KPI/분석 필터 완전 분리
- KPI 계산식 재정의(기간 기준, 이전 기간 비교, 7일 평균 일매출)
- KPI 일자 테이블 + 합계 행 추가
- 상품/옵션 매출 탭 분리 및 TOP 매출 비중 표시(상품명/옵션명 기준)
- 옵션 분석에 `중량단위`, `팩수량`, `환산수량` 표시
- `show_data_grid` 중심 표 출력 통일, 합계 행 강조
- 기본 새로고침은 캐시 유지, 강제 새로고침 버튼 분리
- API 실패 시 직전 성공 데이터 fallback 표시

### 4) 운영 점검 메모
- 동일 프로젝트 내 백엔드/대시보드 서비스-도메인 매핑이 꼬이면 502 fallback 재발 가능
- 점검 우선순위:
  1. 서비스별 도메인 매핑
  2. 각 서비스 배포 아티팩트(루트 vs `streamlit_app`)
  3. 런타임 로그 및 HTTP 15초 fallback 패턴

## 2026-04-17 세션 종료 기록 (대화 연속성용)

### 1) 최종 관측 상태
- 메인 백엔드 도메인 `https://navermodiba-production.up.railway.app`에서 502(`Application failed to respond`) 재현
- 대시보드 도메인 `https://navermodibadashboard-production-5e93.up.railway.app/`는 접속 가능
- 결론적으로 현 시점 장애는 대시보드 렌더링보다는 백엔드 서비스 런타임 이슈 가능성이 큼

### 2) 구조 검증 메모
- 코드 기준으로는 `API 요청 -> 네이버 직접 호출` 경로를 제거하고 DB 조회 중심 구조를 유지
- 대시보드 새로고침 시 네이버 API 재호출이 아니라 백엔드 DB 조회 API에 의존

### 3) 미해결 블로커
- Railway CLI 세션 만료로 로그 조회가 차단됨(`Unauthorized`, non-interactive login 제한)
- 다음 세션 시작 시 사용자 터미널에서 `railway login` 선행 필요

### 4) 다음 세션 즉시 실행 순서
1. `railway login`
2. `railway logs --service naver_modiba`로 부팅 실패 원인 확인
3. 서비스 설정/환경변수/배포 아티팩트 점검 후 재배포
4. `/health`, `/analytics/orders-raw` 정상 응답 확인
