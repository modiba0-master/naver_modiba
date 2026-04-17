---
name: naver-modiba-dashboard
description: >-
  naver_modiba에서 Streamlit 대시보드 기능 추가·누락 보완 시 따를 워크플로.
  streamlit_app 또는 루트 dashboard 수정, analytics API 연동할 때 사용.
---

# naver_modiba 대시보드 작업

## 시작 전
1. 배포/로컬에서 **실행 진입점**이 `streamlit_app/dashboard.py`인지 루트 `dashboard.py`인지 확인한다.
2. 새 화면/지표가 **백엔드 API**가 필요하면 `app/routers/analytics.py`·`app/services/`에 엔드포인트가 있는지 본다.
3. 컬럼·라벨은 `streamlit_app/column_map.py`, `app/column_map.py` 중 어디가 소스인지 확인하고 한 곳으로 맞출지 결정한다.

## 구현 순서 (권장)
1. 데이터 소스 확정: 기존 `GET /analytics/...` 재사용 vs 새 엔드포인트.
2. `services/queries.py` 또는 API 응답 파싱 로직에 집계/필터 추가.
3. Streamlit: `st.session_state` / 필터 위젯 패턴을 기존 페이지와 통일, 라벨은 한국어.
4. 캐시: `st.cache_data` 인자·TTL을 유사한 로더와 맞춘다.
5. 테이블은 `show_data_grid` 등 기존 컴포넌트 확장을 우선한다.

## 하지 말 것
- 루트 `dashboard.py`만 고쳐서 `streamlit_app` 배포본이 갱신되지 않게 두지 않는다(진입점 확인 필수).
- 네이버 API를 대시보드에서 직접 호출하지 않는다 — 백엔드 경유가 이 프로젝트 패턴이다.

## 검증
- 로컬: Streamlit 재실행 후 필터·날짜·빈 데이터 케이스 확인.
- API 변경 시 `tests/test_analytics_api.py` 등 관련 테스트 실행.
