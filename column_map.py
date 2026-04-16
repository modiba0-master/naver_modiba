"""
대시보드·분석 UI용 영문 컬럼명 → 한글 표시명 매핑.
Streamlit·기타 모듈에서 공통으로 import 한다.
"""

from __future__ import annotations

COLUMN_MAP: dict[str, str] = {
    "date": "주문일",
    "payment_date": "결제일시",
    "buyer_name": "구매자명",
    "buyer_id": "구매자ID",
    "receiver_name": "수령인",
    "short_address": "주소",
    "order_count": "주문 수",
    "total_orders": "주문 수",
    "total_sales": "매출",
    "product_name": "상품명",
    "option_name": "옵션명",
    "order_date": "주문일",
    "total_amount": "결제금액",
    "total_quantity": "총 수량",
    "product_group": "상품군",
    "real_quantity": "실제수량",
    "quantity": "수량",
    "amount": "금액",
}

# 한글 헤더 기준 표시 순서(앞에서부터 배치). 여기 없는 컬럼(미매핑 영문 등)은 기존 순서로 뒤에 둔다.
COLUMN_DISPLAY_ORDER: list[str] = [
    "주문일",
    "결제일시",
    "구매자명",
    "구매자ID",
    "수령인",
    "주소",
    "상품명",
    "옵션명",
    "수량",
    "실제수량",
    "결제금액",
]

__all__ = ["COLUMN_MAP", "COLUMN_DISPLAY_ORDER"]
