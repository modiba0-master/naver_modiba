"""순매출·환불 필드 계산 (모델·동기화·분석 공통)."""

from __future__ import annotations


def compute_net_revenue(amount: int, refund_amount: int, cancel_amount: int) -> int:
    """`net_revenue = max(0, amount - refund_amount - cancel_amount)` (합이 amount 초과 시 amount까지만 차감)."""
    gross = max(0, int(amount))
    rf = max(0, int(refund_amount))
    ca = max(0, int(cancel_amount))
    deducted = min(rf + ca, gross)
    return max(0, gross - deducted)


def derive_revenue_status(net_revenue: int, gross_amount: int) -> str:
    """PAID / REFUNDED / CANCELLED — 표시·필터용(영문 코드)."""
    if gross_amount <= 0:
        return "CANCELLED"
    if net_revenue <= 0:
        return "REFUNDED"
    return "PAID"
