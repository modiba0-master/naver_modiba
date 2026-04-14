from datetime import datetime, timedelta
from decimal import Decimal
from random import Random
from typing import List

_rng = Random(42)


def fetch_mock_orders(since: datetime | None = None) -> List[dict]:
    now = datetime.utcnow()
    candidates = []
    for idx in range(3):
        order_time = now - timedelta(minutes=idx * 10)
        amount = Decimal(str(_rng.randint(30000, 300000)))
        cost = (amount * Decimal("0.65")).quantize(Decimal("0.01"))
        shipping = Decimal("3000.00")
        candidates.append(
            {
                "order_id": f"MOCK-{now.strftime('%Y%m%d%H%M%S')}-{idx}",
                "customer_id": f"CUST-{_rng.randint(1, 5)}",
                "customer_name": f"Customer {_rng.randint(1, 5)}",
                "order_date": order_time,
                "amount": amount.quantize(Decimal("0.01")),
                "cost": cost,
                "shipping_fee": shipping,
            }
        )

    if since is None:
        return candidates
    return [item for item in candidates if item["order_date"] > since]
