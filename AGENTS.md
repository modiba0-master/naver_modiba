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
