"""
Streamlit 표(st.dataframe)에 쓸 **표시용** 영문 → 한글 헤더 매핑.

- 네이버 API 동기화, FastAPI `/analytics/*` JSON, DB 컬럼명은 **영문 스키마 그대로** 유지한다.
- 이 모듈은 `streamlit_app/services/data_grid.py` 등 **UI 그리드 직전**에서만 사용한다.
- FastAPI `app` 라우터·서비스·동기화 코드에서는 import하지 않는다.
"""

from __future__ import annotations

COLUMN_MAP: dict[str, str] = {
    "date": "날자",
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
    "order_date": "날자",
    "total_amount": "주문금액",
    "total_quantity": "총 수량",
    "product_group": "상품군",
    "real_quantity": "수량집계",
    "quantity": "수량",
    "amount": "금액",
    # 분석·레거시 대시보드용
    "revenue": "매출",
    "orders": "주문 수",
    "cancel_count": "취소건수",
    "cancel_rate_pct": "취소율(%)",
    "multiplier": "배수",
    "weight_unit": "중량단위",
    "pack_count": "팩수량",
    "pack_count_sum": "팩수량 합계",
    "converted_quantity": "환산수량",
    "address": "전체주소",
    "business_date": "영업일",
    "order_status": "주문상태",
    "today_revenue": "오늘 매출",
    "total_profit": "총 이익",
    "profit": "이익",
    "hour_of_day": "시간(시)",
    "weekday_num": "요일번호",
    "weekday": "요일",
}

# 한글 헤더 기준 표시 순서(앞에서부터 배치). 여기 없는 컬럼(미매핑 영문 등)은 기존 순서로 뒤에 둔다.
COLUMN_DISPLAY_ORDER: list[str] = [
    "날자",
    "결제일시",
    "구매자명",
    "구매자ID",
    "수령인",
    "주소",
    "상품명",
    "옵션상품명",
    "수량",
    "수량집계",
    "주문금액",
    "영업일",
    "주문상태",
]

__all__ = ["COLUMN_MAP", "COLUMN_DISPLAY_ORDER"]
