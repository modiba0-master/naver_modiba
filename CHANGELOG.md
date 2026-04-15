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
