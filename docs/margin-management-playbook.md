# Margin Management Playbook (Option Level)

## Purpose

운영자가 다음 질문에 매일 답할 수 있도록 설계한다.

1. 잘 팔리고 있는데 이익도 남는가?
2. 무료배송/유료배송 정책이 마진에 어떤 영향을 주는가?
3. 어떤 옵션이 매출은 내지만 마진을 깎는가?

원칙: 기존 `orders`는 원본 SoT로 유지하고, 마진 계산은 파생 테이블에서만 수행한다.

---

## New Tables

### 1) `product_option_cost_master`

옵션별 원가 마스터(기간 이력 포함)

- 핵심 컬럼
  - `product_name`, `option_name`, `option_code`
  - `unit_cost` (옵션 1개 기준 원가)
  - `pack_cost` (포장/부자재)
  - `fulfillment_cost` (출고 관련 운영비)
  - `default_shipping_cost` (배송비 원가 기준값)
  - `effective_from`, `effective_to`
- 운영 포인트
  - 원가 변경 시 기존 행 수정이 아니라 새 기간 행 추가
  - `effective_to`로 과거 재현 가능

### 2) `shipping_margin_rule`

유료/무료배송 정책에 따른 마진 처리 규칙

- 핵심 컬럼
  - `delivery_fee_type` (예: FREE/PAID/BUNDLE)
  - `customer_paid_shipping`
  - `seller_shipping_burden`
  - `margin_treatment` (`include_shipping`, `exclude_shipping_revenue`)
  - `effective_from`, `effective_to`
- 운영 포인트
  - 실제 택배 원가 변경 시 기간 이력으로 관리
  - 도서산간/묶음배송 정책은 별도 rule 추가

### 3) `agg_option_margin_daily`

대시보드 표시용 옵션 단위 마진 스냅샷

- 핵심 컬럼
  - `stat_date`, `product_name`, `option_name`
  - `order_count`, `order_quantity`
  - `net_revenue`, `expected_settlement_amount`
  - `customer_paid_shipping`, `seller_shipping_burden`
  - `estimated_cost`, `margin_amount`, `margin_rate_pct`

---

## Daily Pipeline

1. `orders`에서 대상 기간 주문 로드
2. `product_option_cost_master` 기간 매칭
3. `shipping_margin_rule` 기간/배송유형 매칭
4. 옵션 단위 비용/마진 계산
5. `agg_option_margin_daily` UPSERT
6. 검증 SQL 실행

---

## Calculation Standard

옵션 라인 기준:

- `estimated_cost`
  - `unit_cost * order_quantity`
  - `+ pack_cost + fulfillment_cost`
  - `+ seller_shipping_burden`
- `margin_amount`
  - `net_revenue - estimated_cost`
- `margin_rate_pct`
  - `margin_amount / net_revenue * 100`
  - `net_revenue = 0`이면 0 처리

배송비는 보고서에서 2개 뷰를 권장:

- 상품마진(배송 제외)
- 배송포함 마진(실질 손익)

---

## Dashboard Mapping (4th Stage)

### KPI Cards

- 총 순매출
- 총 정산예정금액
- 총 추정원가
- 총 마진액
- 평균 마진율(%)

### Table (Option Margin Top/Bottom)

- 상품명
- 옵션명
- 주문건수
- 주문수량
- 순매출
- 추정원가
- 마진액
- 마진율(%)
- 배송유형

### Alert Rules

- 마진율 < 10%: 경고
- 마진율 < 0%: 긴급
- 전주 대비 마진율 -5%p 이하: 원인 분석 대상

---

## First 5 Sample Inputs

아래 5개부터 입력 후 검증 권장:

1. 주력옵션 A (유료배송)
2. 주력옵션 B (무료배송)
3. 묶음배송 옵션
4. 저마진 의심 옵션
5. 신규 테스트 옵션

입력 후 `agg_option_margin_daily`에서 마진율이 직관과 맞는지 먼저 확인한다.

---

## Validation Checklist

- `orders` 합계 매출과 `agg_option_margin_daily` 매출이 일치하는가?
- 비용이 과도하게 0으로 들어간 옵션은 없는가?
- 무료배송/유료배송 rule 적용 결과가 정책과 일치하는가?
- 마진 음수 옵션이 실제 프로모션/정책 결과인지 데이터 오류인지 확인했는가?

