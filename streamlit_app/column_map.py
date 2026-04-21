"""
Streamlit 표(st.dataframe)에 쓸 표시용 영문 -> 한글 헤더 매핑.

- 네이버 API 동기화, FastAPI `/analytics/*` JSON, DB 컬럼명은 영문 스키마 그대로 유지한다.
- 이 모듈은 `services/data_grid.py` 등 UI 그리드 직전에서만 사용한다.
"""

from __future__ import annotations

COLUMN_MAP: dict[str, str] = {
    "order_id": "상품주문번호",
    "date": "귀속일(달력)",
    "aggregation_window_kst": "매출집계구간(KST)",
    "payment_date": "결제일시",
    "buyer_name": "구매자명",
    "buyer_id": "구매자ID",
    "receiver_name": "수령인",
    "short_address": "주소",
    "order_count": "주문수량",
    "total_orders": "주문 수",
    "total_sales": "매출",
    "product_name": "상품명",
    "option_name": "옵션상품명",
    "order_date": "주문일(달력)",
    "total_amount": "주문금액",
    "total_quantity": "총 수량",
    "product_group": "상품군",
    "real_quantity": "수량집계",
    "quantity": "수량",
    "amount": "금액",
    "revenue": "매출",
    "orders": "주문 수",
    "cancel_count": "취소건수",
    "cancel_rate_pct": "취소율(%)",
    "multiplier": "배수",
    "address": "전체주소",
    "business_date": "매출 귀속일",
    "order_calendar_date": "주문일(날짜)",
    "ordered_at": "주문일시",
    "placed_order_at": "발주처리일시",
    "shipped_at": "발송처리일시",
    "order_status": "주문상태",
    "content_order_no": "주문번호",
    "today_revenue": "오늘 매출",
    "total_profit": "총 이익",
    "profit": "이익",
    "hour_of_day": "시간(시)",
    "weekday_num": "요일번호",
    "weekday": "요일",
}

COLUMN_DISPLAY_ORDER: list[str] = [
    "귀속일(달력)",
    "주문금액",
    "주문수량",
    "총 수량",
    "매출집계구간(KST)",
    "날자",
    "상품주문번호",
    "주문번호",
    "주문일(날짜)",
    "주문일시",
    "발주처리일시",
    "발송처리일시",
    "결제일시",
    "구매자명",
    "구매자ID",
    "수령인",
    "주소",
    "상품명",
    "옵션상품명",
    "수량",
    "수량집계",
    "영업일",
    "주문상태",
]

__all__ = ["COLUMN_MAP", "COLUMN_DISPLAY_ORDER"]
