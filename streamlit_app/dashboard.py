from __future__ import annotations

import re
import os
import sys
import time
import hmac
from typing import Any
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pandas as pd
import streamlit as st
from sqlalchemy import text

try:
    from streamlit_autorefresh import st_autorefresh
except Exception:  # pragma: no cover - optional component in deploy
    st_autorefresh = None

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from services.data_grid import show_data_grid
from services.db import SessionLocal
from services.kpi_from_filtered import (
    delta_rate,
    expected_sales_from_recent_7d,
    kpi_aggregate,
)
from services.kpi_ui import add_avg_ticket_to_daily, append_daily_total_row
from ui_theme import (
    apply_dashboard_theme,
    render_page_title,
    section_heading,
)

# HTTP로 주문 목록·DB 통계를 가져온다. 잘못된 URL이면 "어제와 같은 결과"처럼 보인다(다른/옛 백엔드 DB).
# Railway: Streamlit 서비스에 `ANALYTICS_API_BASE_URL`로 실제 FastAPI(동기화+MariaDB) URL을 맞출 것.
_PRODUCTION_API = "https://web-production-0001b.up.railway.app"
DEFAULT_API_BASE_URL = (os.environ.get("ANALYTICS_API_BASE_URL") or _PRODUCTION_API).strip().rstrip(
    "/"
)
REQUIRED_COLUMNS = [
    "order_id",
    "date",
    "payment_date",
    "buyer_name",
    "buyer_id",
    "receiver_name",
    "address",
    "product_name",
    "option_name",
    "quantity",
    "amount",
]
KST = ZoneInfo("Asia/Seoul")
HTTP_TIMEOUT = httpx.Timeout(connect=5.0, read=20.0, write=20.0, pool=5.0)
HTTP_RETRY_ATTEMPTS = 3
HTTP_RETRY_BACKOFF_SECONDS = 0.8
# `/analytics/orders-raw` 조회 상한(영업일). 고객탭 3개월 패턴 분석까지 포함해 여유를 둔다.
ORDERS_RAW_FETCH_DAYS = 95
# 경영요약 상승/하락 표: 동일 높이로 PC에서 하단 정렬이 맞도록 Glide 표 높이(px).
SUMMARY_RISE_FALL_DATAFRAME_HEIGHT_PX = 320

_WEEKDAY_KO = ("월", "화", "수", "목", "금", "토", "일")


def _kst_anchor_business_date() -> date:
    """대시보드 KPI 기준일과 동일: 16시 이후면 익일 영업일 달력."""
    now_kst = datetime.now(KST)
    return now_kst.date() + timedelta(days=1) if now_kst.hour >= 16 else now_kst.date()


_OPTION_PRODUCT_JOIN_SEP = " — "


def _option_product_label(product_name: object, option_name: object) -> str:
    """집계 키: 상품명(+ 구분자 + 옵션명). 옵션 없으면 상품명만."""
    pn = str(product_name or "").strip()
    on = str(option_name or "").strip()
    if not on or on.lower() == "nan":
        return pn
    return f"{pn}{_OPTION_PRODUCT_JOIN_SEP}{on}"


def _option_grid_display_text(key_label: object) -> str:
    """그리드 표시: 옵션 있으면 옵션명만, 없으면 `상품명 (통합)`."""
    s = str(key_label or "").strip()
    if not s:
        return ""
    if _OPTION_PRODUCT_JOIN_SEP not in s:
        return f"{s} (통합)"
    left, _sep, right = s.partition(_OPTION_PRODUCT_JOIN_SEP)
    left, right = left.strip(), right.strip()
    if right and right.lower() != "nan":
        return right
    return f"{left} (통합)" if left else ""


def _option_name_display(option_name: object) -> str:
    """옵션명 단독 표시(비어 있으면 대체 문구)."""
    s = str(option_name or "").strip()
    return s if s else "(옵션없음)"


def _option_norm_key(option_name: object) -> str:
    """옵션 문자열을 규격 키로 정규화한다.

    예)
    - 5kg (1kgX5팩), 5kg(1X5팩), 5kg(1kgX5) -> w5000_u1000_c5
    - 5kg(500gX10팩) -> w5000_u500_c10
    """
    raw = str(option_name or "").strip().lower()
    if not raw:
        return "unknown_option"
    s = raw.replace(" ", "")
    s = s.replace("×", "x").replace("*", "x").replace("팩", "")
    s = s.replace("[", "(").replace("]", ")")
    total_match = re.search(r"(\d+(?:\.\d+)?)kg", s)
    total_g: int | None = None
    if total_match:
        total_g = int(round(float(total_match.group(1)) * 1000))
    detail_match = re.search(r"\((\d+(?:\.\d+)?)(kg|g)?x(\d+)\)", s)
    if detail_match:
        unit_num = float(detail_match.group(1))
        unit_type = detail_match.group(2) or "kg"
        unit_g = int(round(unit_num * 1000)) if unit_type == "kg" else int(round(unit_num))
        count = int(detail_match.group(3))
        if total_g is None:
            total_g = unit_g * count
        return f"w{total_g}_u{unit_g}_c{count}"
    fallback = re.sub(r"[^a-z0-9가-힣]+", "_", s).strip("_")
    return fallback or "unknown_option"


def _format_sales_date_label(d: date) -> str:
    """매출 집계일(달력) + 요일 — KPI 일자 행에 통일 표기."""
    return f"{d.isoformat()} ({_WEEKDAY_KO[d.weekday()]})"


def _format_sales_date_compact(value: object) -> str:
    """상세 원장용 매출 집계일 표기: M/D (요일)."""
    ts = pd.to_datetime(value, errors="coerce")
    if pd.isna(ts):
        return ""
    d = ts.date()
    return f"{d.month}/{d.day} ({_WEEKDAY_KO[d.weekday()]})"


def _format_month_day(value: object) -> str:
    """간단 날짜 표기: M/D."""
    ts = pd.to_datetime(value, errors="coerce")
    if pd.isna(ts):
        return ""
    d = ts.date()
    return f"{d.month}/{d.day}"


def _aggregate_kpi_daily(kpi_daily_table: pd.DataFrame) -> pd.DataFrame:
    """매출 집계일별 주문금액·건수·수량 합계 (`date` = 저장된 `business_date`)."""
    if kpi_daily_table.empty:
        return pd.DataFrame(
            columns=[
                "date",
                "total_amount",
                "order_count",
                "total_quantity",
            ]
        )
    df = kpi_daily_table.copy()
    if "net_revenue" in df.columns:
        df["_amount"] = pd.to_numeric(df["net_revenue"], errors="coerce").fillna(0.0)
    else:
        df["_amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0.0)
    out_rows: list[dict] = []
    for d, g in df.groupby("date"):
        a = g["_amount"]
        total = float(a.sum())
        oid = int(g["order_id"].astype(str).replace("", pd.NA).dropna().nunique())
        tq = float(pd.to_numeric(g["quantity"], errors="coerce").fillna(0).sum())
        out_rows.append(
            {
                "date": d,
                "total_amount": total,
                "order_count": oid,
                "total_quantity": tq,
            }
        )
    return pd.DataFrame(out_rows)


def format_krw(value: float | int) -> str:
    return f"{float(value):,.0f}원"


def format_now_kst() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST")


def extract_multiplier(option_name: str) -> int:
    if not isinstance(option_name, str) or not option_name.strip():
        return 1
    patterns = [r"x\s*(\d+)", r"(\d+)\s*개", r"(\d+)\s*팩"]
    for pattern in patterns:
        match = re.search(pattern, option_name, re.IGNORECASE)
        if match:
            return int(match.group(1))
    return 1


def extract_weight_unit(option_name: str) -> str:
    if not isinstance(option_name, str) or not option_name.strip():
        return ""
    m = re.search(r"(\d+(?:\.\d+)?)\s*(kg|g)", option_name, re.IGNORECASE)
    if not m:
        return ""
    return f"{m.group(1)}{m.group(2).lower()}"


def _is_icepack_size_16x23(product_name: object) -> bool:
    """아이스팩 16x23 규격 상품은 수량 배수 보정 대상."""
    text = str(product_name or "")
    return ("아이스팩" in text) and bool(re.search(r"16\s*[xX]\s*23", text))


def product_group(product_name: str) -> str:
    text = product_name if isinstance(product_name, str) else ""
    if "닭가슴살" in text:
        return "닭가슴살"
    if "닭안심" in text:
        return "닭안심"
    return "기타"


def _http_get_json_with_retry(
    url: str,
    *,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """일시 장애(타임아웃/네트워크/429/5xx)에 대해 짧은 재시도."""
    last_error: Exception | None = None
    for attempt in range(1, HTTP_RETRY_ATTEMPTS + 1):
        try:
            response = httpx.get(url, params=params, timeout=HTTP_TIMEOUT)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code if exc.response is not None else None
            retryable = status == 429 or (status is not None and status >= 500)
            if not retryable:
                raise
            last_error = exc
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_error = exc

        if attempt < HTTP_RETRY_ATTEMPTS:
            time.sleep(HTTP_RETRY_BACKOFF_SECONDS * attempt)

    if last_error is not None:
        raise last_error
    raise RuntimeError("HTTP request failed without captured exception")


@st.cache_data(ttl=60)
def fetch_order_data(
    base_url: str,
    revenue_basis: str = "payment",
    start_date: date | None = None,
    end_date: date | None = None,
) -> pd.DataFrame:
    url = base_url.rstrip("/")
    params: dict[str, Any] = {"revenue_basis": revenue_basis}
    if start_date is not None and end_date is not None:
        params["start_date"] = datetime.combine(start_date, time.min, tzinfo=KST).isoformat()
        params["end_date"] = datetime.combine(end_date, time(23, 59, 59), tzinfo=KST).isoformat()
    payload = _http_get_json_with_retry(f"{url}/analytics/orders-raw", params=params)
    items = payload.get("items", [])
    return pd.DataFrame(items)


@st.cache_data(ttl=15)
def fetch_db_stats(base_url: str) -> dict[str, object]:
    """`/analytics/db-stats` — 원장 건수·최신 결제일시(실시간 DB 확인용)."""
    url = base_url.rstrip("/")
    return _http_get_json_with_retry(f"{url}/analytics/db-stats")


def _mark_api_success() -> None:
    st.session_state["api_last_success_at"] = format_now_kst()
    st.session_state["api_consecutive_failures"] = 0
    st.session_state["api_last_error"] = ""


def _mark_api_failure(exc: Exception) -> None:
    st.session_state["api_consecutive_failures"] = int(
        st.session_state.get("api_consecutive_failures", 0)
    ) + 1
    st.session_state["api_last_error"] = str(exc)[:180]


def _render_api_health_caption() -> None:
    last_ok = st.session_state.get("api_last_success_at", "—")
    fail_count = int(st.session_state.get("api_consecutive_failures", 0))
    last_error = st.session_state.get("api_last_error", "")
    st.write(f"API 최근 성공: {last_ok} · 연속 실패: {fail_count}회")
    if fail_count > 0 and last_error:
        st.write(f"최근 오류: {last_error}")


def _safe_autorefresh(interval_ms: int, key: str) -> None:
    """컴포넌트 로딩 실패 시에도 앱이 중단되지 않게 보호."""
    if st_autorefresh is None:
        return
    try:
        st_autorefresh(interval=interval_ms, key=key)
    except Exception:
        # 배포 네트워크/프록시 이슈로 component asset 로드가 실패할 수 있다.
        # 자동 새로고침만 비활성화하고 나머지 대시보드는 정상 동작시킨다.
        if not st.session_state.get("_autorefresh_component_warned", False):
            st.warning("자동 새로고침 컴포넌트 로딩에 실패해 자동 새로고침을 비활성화했습니다.")
            st.session_state["_autorefresh_component_warned"] = True


def _normalize_api_column_name(name: object) -> str:
    """API 응답 컬럼명을 내부 표준 snake_case로 정규화."""
    text = str(name).strip()
    text = re.sub(r"(?<!^)(?=[A-Z])", "_", text)
    text = text.replace("-", "_").replace(" ", "_")
    text = re.sub(r"_+", "_", text).strip("_").lower()
    return text


def _normalize_api_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """네이버/백엔드 응답 컬럼 alias를 내부 분석 컬럼으로 통일."""
    df = frame.copy()
    df.columns = [_normalize_api_column_name(col) for col in df.columns]

    alias_map = {
        "orderer_name": "buyer_name",
        "orderer_id": "buyer_id",
        "shipping_address": "address",
        "receiver_address": "address",
        "expectedsettlementamount": "expected_settlement_amount",
    }
    for src, dst in alias_map.items():
        if src in df.columns and dst not in df.columns:
            df = df.rename(columns={src: dst})

    if "date" not in df.columns and "business_date" in df.columns:
        df = df.rename(columns={"business_date": "date"})
    return df


@st.cache_data(ttl=60)
def get_today_summary(today: date) -> dict[str, int]:
    """
    orders 테이블 기준 오늘 날짜 주문 건수·매출 합계.
    스키마상 금액 컬럼은 `amount`이므로 SUM(amount)로 집계한다.
    """
    db = SessionLocal()
    try:
        row = db.execute(
            text(
                """
                SELECT
                    COUNT(*) AS order_count,
                    COALESCE(SUM(net_revenue), 0) AS total_sales
                FROM orders
                WHERE business_date = :today
                """
            ),
            {"today": today},
        ).one()
        return {
            "order_count": int(row.order_count or 0),
            "total_sales": int(row.total_sales or 0),
        }
    finally:
        db.close()


@st.cache_data(ttl=300)
def get_daily_summary(selected_date: date) -> dict[str, int]:
    """
    daily_summary 테이블에서 선택일의 주문·매출 합계.
    테이블은 (date, product_id, option_id) 단위 행이므로 SUM으로 일 합계를 낸다.
    """
    db = SessionLocal()
    try:
        row = db.execute(
            text(
                """
                SELECT
                    COALESCE(SUM(orders), 0) AS total_orders,
                    COALESCE(SUM(revenue), 0) AS total_sales
                FROM daily_summary
                WHERE date = :date
                """
            ),
            {"date": selected_date},
        ).one()
        return {
            "total_orders": int(row.total_orders or 0),
            "total_sales": int(row.total_sales or 0),
        }
    finally:
        db.close()


def normalize_order_data(frame: pd.DataFrame) -> pd.DataFrame:
    # 로드 직후 컬럼명을 통일해 이후 집계/표시에 동일 스키마를 사용한다.
    df = _normalize_api_columns(frame)
    for col in REQUIRED_COLUMNS:
        if col not in df.columns:
            df[col] = ""

    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    # 결제일시: 원본(payment_date). 매출 집계일 `date`는 저장된 business_date(16시 규칙).
    df["payment_date"] = pd.to_datetime(df["payment_date"], errors="coerce")
    df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce").fillna(0)
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0)
    if "expected_settlement_amount" not in df.columns:
        df["expected_settlement_amount"] = 0
    df["expected_settlement_amount"] = pd.to_numeric(
        df["expected_settlement_amount"], errors="coerce"
    ).fillna(0)
    if "net_revenue" not in df.columns:
        df["net_revenue"] = df["amount"]
    else:
        df["net_revenue"] = pd.to_numeric(df["net_revenue"], errors="coerce").fillna(0)
    df["multiplier"] = df["option_name"].apply(extract_multiplier)
    # 아이스팩 16x23 규격명은 "x23"이 수량 배수가 아니라 사이즈 표기이므로 1개로 취급.
    icepack_mask = df["product_name"].apply(_is_icepack_size_16x23)
    if icepack_mask.any():
        df.loc[icepack_mask, "multiplier"] = 1
    df["real_quantity"] = df["quantity"] * df["multiplier"]
    df["weight_unit"] = df["option_name"].apply(extract_weight_unit)
    df["pack_count"] = df["multiplier"]
    df["converted_quantity"] = df["quantity"] * df["pack_count"]
    df["short_address"] = df["address"].astype(str).str.slice(0, 20)
    if "customer_id" not in df.columns:
        df["customer_id"] = df["buyer_id"]
    df["option_product_label"] = [
        _option_product_label(a, b) for a, b in zip(df["product_name"], df["option_name"])
    ]
    df = df.dropna(subset=["date"]).copy()
    return df


def _prepare_analysis_summary(
    frame: pd.DataFrame,
    *,
    group_key: str,
    revenue_column: str,
) -> pd.DataFrame:
    grouped = (
        frame.groupby(group_key, as_index=False)
        .agg(
            total_amount=(revenue_column, "sum"),
            order_quantity=("quantity", "sum"),
            sold_quantity=("real_quantity", "sum"),
            order_count=(
                "order_id",
                lambda s: s.astype(str).replace("", pd.NA).dropna().nunique(),
            ),
        )
        .sort_values("total_amount", ascending=False)
    )
    total_amount_sum = float(grouped["total_amount"].sum()) if not grouped.empty else 0.0
    if total_amount_sum > 0:
        grouped["sales_share_pct"] = grouped["total_amount"] / total_amount_sum * 100.0
    else:
        grouped["sales_share_pct"] = 0.0
    grouped["amount_per_order"] = grouped["total_amount"] / grouped["order_count"].replace(0, pd.NA)
    grouped["amount_per_order"] = grouped["amount_per_order"].fillna(0.0)
    return grouped


def _append_analysis_total_row(
    summary_df: pd.DataFrame,
    *,
    name_col: str,
) -> pd.DataFrame:
    """상품/옵션 분석표 하단 합계 행 추가."""
    if summary_df.empty:
        return summary_df
    body = summary_df.copy()
    total_amount = float(pd.to_numeric(body["total_amount"], errors="coerce").fillna(0).sum())
    total_orders = int(pd.to_numeric(body["order_count"], errors="coerce").fillna(0).sum())
    total_order_quantity = float(
        pd.to_numeric(body["order_quantity"], errors="coerce").fillna(0).sum()
    )
    total_sold = float(pd.to_numeric(body["sold_quantity"], errors="coerce").fillna(0).sum())
    avg_amount_per_order = (total_amount / total_orders) if total_orders > 0 else 0.0
    total_row = pd.DataFrame(
        [
            {
                name_col: "합계",
                "order_count": total_orders,
                "order_quantity": total_order_quantity,
                "total_amount": total_amount,
                "sales_share_pct": 100.0 if total_amount > 0 else 0.0,
                "sold_quantity": total_sold,
                "amount_per_order": avg_amount_per_order,
            }
        ]
    )
    return pd.concat([body, total_row], ignore_index=True)


def _prepare_detail_ledger_for_display(
    frame: pd.DataFrame,
) -> tuple[pd.DataFrame, str | None]:
    """상세 주문 원장 표시 규칙: 집계일 포맷/일부 컬럼 숨김/안내문 상단 분리."""
    ledger = frame.copy()
    guidance: str | None = None

    if "aggregation_window_kst" in ledger.columns:
        guide_series = ledger["aggregation_window_kst"].dropna().astype(str).str.strip()
        guide_series = guide_series[guide_series != ""]
        if not guide_series.empty:
            guidance = guide_series.iloc[0]
        ledger = ledger.drop(columns=["aggregation_window_kst"])

    if "content_order_no" in ledger.columns:
        ledger = ledger.drop(columns=["content_order_no"])

    if "date" in ledger.columns:
        ledger["date"] = ledger["date"].map(_format_sales_date_compact)

    return ledger, guidance


def _daily_summary_from_orders(frame: pd.DataFrame, target_date: date) -> dict[str, float]:
    day_df = frame[frame["date"].dt.date == target_date].copy()
    if day_df.empty:
        return {
            "total_amount": 0.0,
            "order_count": 0.0,
            "order_quantity": 0.0,
            "sold_quantity": 0.0,
            "customer_count": 0.0,
        }
    amount_col = "net_revenue" if "net_revenue" in day_df.columns else "amount"
    return {
        "total_amount": float(pd.to_numeric(day_df[amount_col], errors="coerce").fillna(0).sum()),
        "order_count": float(day_df["order_id"].astype(str).replace("", pd.NA).dropna().nunique()),
        "order_quantity": float(pd.to_numeric(day_df["quantity"], errors="coerce").fillna(0).sum()),
        "sold_quantity": float(pd.to_numeric(day_df["real_quantity"], errors="coerce").fillna(0).sum()),
        "customer_count": float(day_df["buyer_id"].astype(str).replace("", pd.NA).dropna().nunique()),
    }


def _product_revenue_delta_table(
    frame: pd.DataFrame,
    current_date: date,
    compare_date: date,
) -> pd.DataFrame:
    amount_col = "net_revenue" if "net_revenue" in frame.columns else "amount"
    key = "option_product_label"
    curr = (
        frame[frame["date"].dt.date == current_date]
        .groupby(key, as_index=False)
        .agg(current_revenue=(amount_col, "sum"))
    )
    prev = (
        frame[frame["date"].dt.date == compare_date]
        .groupby(key, as_index=False)
        .agg(prev_revenue=(amount_col, "sum"))
    )
    merged = curr.merge(prev, on=key, how="outer").fillna(0.0)
    if merged.empty:
        return merged
    merged["revenue_diff"] = merged["current_revenue"] - merged["prev_revenue"]
    merged["revenue_diff_pct"] = merged.apply(
        lambda r: ((r["revenue_diff"] / r["prev_revenue"]) * 100.0) if r["prev_revenue"] > 0 else 0.0,
        axis=1,
    )
    return merged.sort_values("revenue_diff", ascending=False)


def _append_summary_delta_total_row(summary_df: pd.DataFrame) -> pd.DataFrame:
    """경영요약 상승/하락 표 하단 합계 행 추가."""
    if summary_df.empty:
        return summary_df
    body = summary_df.copy()
    total_row = pd.DataFrame(
        [
            {
                "option_product_label": "합계",
                "current_revenue": float(
                    pd.to_numeric(body["current_revenue"], errors="coerce").fillna(0).sum()
                ),
                "prev_revenue": float(
                    pd.to_numeric(body["prev_revenue"], errors="coerce").fillna(0).sum()
                ),
                "revenue_diff": float(
                    pd.to_numeric(body["revenue_diff"], errors="coerce").fillna(0).sum()
                ),
                "revenue_diff_pct": 0.0,
            }
        ]
    )
    base_prev = float(total_row.at[0, "prev_revenue"])
    if base_prev > 0:
        total_row.at[0, "revenue_diff_pct"] = float(total_row.at[0, "revenue_diff"]) / base_prev * 100.0
    return pd.concat([body, total_row], ignore_index=True)


def _sorted_business_dates_up_to(frame: pd.DataFrame, report_date: date) -> list[date]:
    s = frame["date"].dt.date
    return sorted({d for d in s.dropna().unique() if d <= report_date})


def _option_avg_daily_in_tail_window(
    frame: pd.DataFrame,
    *,
    amount_col: str,
    report_date: date,
    calendar_days: int,
    key_col: str,
) -> pd.DataFrame:
    """최근 min(calendar_days, 로드된 영업일 수)일에서 옵션상품명별 일평균 매출(누적합/달력일수)."""
    dates = _sorted_business_dates_up_to(frame, report_date)
    if not dates:
        return pd.DataFrame(columns=[key_col, "avg_daily"])
    use_n = min(int(calendar_days), len(dates))
    tail = set(dates[-use_n:])
    df = frame.assign(_d=frame["date"].dt.date)
    df = df[df["_d"].isin(tail)]
    if df.empty:
        return pd.DataFrame(columns=[key_col, "avg_daily"])
    g = df.groupby(key_col, as_index=False).agg(_total=(amount_col, "sum"))
    g["avg_daily"] = g["_total"] / float(use_n)
    return g[[key_col, "avg_daily"]]


def _simple_nextday_forecast(frame: pd.DataFrame, report_date: date) -> tuple[float, str]:
    amount_col = "net_revenue" if "net_revenue" in frame.columns else "amount"
    daily = (
        frame[frame["date"].dt.date <= report_date]
        .assign(_d=lambda df: df["date"].dt.date)
        .groupby("_d", as_index=False)
        .agg(total_amount=(amount_col, "sum"))
        .sort_values("_d")
    )
    if daily.empty:
        return 0.0, "하"
    recent = daily.tail(7)
    forecast = float(pd.to_numeric(recent["total_amount"], errors="coerce").fillna(0).mean())
    confidence = "상" if len(recent) >= 7 else ("중" if len(recent) >= 4 else "하")
    return forecast, confidence


def _build_product_insight_table(
    frame: pd.DataFrame,
    report_date: date,
    compare_date: date,
) -> pd.DataFrame:
    """옵션상품명별 증감, 원인 추정, 단기·중기·보합·상한 예측 참고(로드된 DB 구간만 사용)."""
    amount_col = "net_revenue" if "net_revenue" in frame.columns else "amount"
    key = "option_product_label"
    df = frame.copy()
    if key not in df.columns:
        df[key] = [_option_product_label(a, b) for a, b in zip(df["product_name"], df["option_name"])]
    df["_biz_date"] = df["date"].dt.date

    curr = (
        df[df["_biz_date"] == report_date]
        .groupby(key, as_index=False)
        .agg(
            today_revenue=(amount_col, "sum"),
            today_order_qty=("quantity", "sum"),
            today_sold_qty=("real_quantity", "sum"),
            today_order_count=("order_id", lambda s: s.astype(str).replace("", pd.NA).dropna().nunique()),
            today_customer_count=("buyer_id", lambda s: s.astype(str).replace("", pd.NA).dropna().nunique()),
        )
    )
    prev = (
        df[df["_biz_date"] == compare_date]
        .groupby(key, as_index=False)
        .agg(
            prev_revenue=(amount_col, "sum"),
            prev_order_qty=("quantity", "sum"),
            prev_order_count=("order_id", lambda s: s.astype(str).replace("", pd.NA).dropna().nunique()),
            prev_customer_count=("buyer_id", lambda s: s.astype(str).replace("", pd.NA).dropna().nunique()),
        )
    )
    merged = curr.merge(prev, on=key, how="outer").fillna(0.0)
    if merged.empty:
        return merged

    merged["today_avg_price"] = merged["today_revenue"] / merged["today_order_qty"].replace(0, pd.NA)
    merged["prev_avg_price"] = merged["prev_revenue"] / merged["prev_order_qty"].replace(0, pd.NA)
    merged["today_avg_price"] = merged["today_avg_price"].fillna(0.0)
    merged["prev_avg_price"] = merged["prev_avg_price"].fillna(0.0)

    merged["revenue_diff"] = merged["today_revenue"] - merged["prev_revenue"]
    merged["revenue_diff_pct"] = merged.apply(
        lambda r: ((r["revenue_diff"] / r["prev_revenue"]) * 100.0) if r["prev_revenue"] > 0 else 0.0,
        axis=1,
    )
    merged["price_diff_pct"] = merged.apply(
        lambda r: ((r["today_avg_price"] - r["prev_avg_price"]) / r["prev_avg_price"] * 100.0)
        if r["prev_avg_price"] > 0
        else 0.0,
        axis=1,
    )

    short_df = _option_avg_daily_in_tail_window(
        df, amount_col=amount_col, report_date=report_date, calendar_days=7, key_col=key
    ).rename(columns={"avg_daily": "forecast_short"})
    med_df = _option_avg_daily_in_tail_window(
        df, amount_col=amount_col, report_date=report_date, calendar_days=30, key_col=key
    ).rename(columns={"avg_daily": "forecast_medium"})
    merged = merged.merge(short_df, on=key, how="left")
    merged = merged.merge(med_df, on=key, how="left")
    merged["forecast_short"] = pd.to_numeric(merged["forecast_short"], errors="coerce").fillna(0.0)
    merged["forecast_medium"] = pd.to_numeric(merged["forecast_medium"], errors="coerce").fillna(0.0)
    merged["forecast_balanced"] = (merged["forecast_short"] + merged["forecast_medium"]) / 2.0
    merged["forecast_upper"] = merged[["forecast_short", "forecast_medium", "today_revenue"]].max(axis=1)

    def _reason(row: pd.Series) -> str:
        reasons: list[str] = []
        if abs(float(row["price_diff_pct"])) >= 5.0:
            direction = "상승" if float(row["price_diff_pct"]) > 0 else "하락"
            reasons.append(f"평균 판매가 {direction}")
        qty_diff = float(row["today_order_qty"]) - float(row["prev_order_qty"])
        if abs(qty_diff) >= 3:
            reasons.append("주문수량 변화")
        cust_diff = float(row["today_customer_count"]) - float(row["prev_customer_count"])
        if abs(cust_diff) >= 2:
            reasons.append("고객 유입/재구매 변화")
        if not reasons:
            reasons.append("기본 변동 범위")
        return " + ".join(reasons[:2])

    merged["change_reason"] = merged.apply(_reason, axis=1)
    return merged.sort_values("revenue_diff", ascending=False)


def _safe_pct_change(curr: float, prev: float) -> float:
    if prev == 0:
        return 0.0
    return ((curr - prev) / prev) * 100.0


def _forecast_confidence_label(active_days: int) -> str:
    if active_days >= 20:
        return "상"
    if active_days >= 10:
        return "중"
    return "하"


def _build_option_trend_snapshot(frame: pd.DataFrame, base_date: date) -> tuple[pd.DataFrame, pd.DataFrame]:
    """옵션상품명 기준 일/주/월 환산수량·매출 비교표 생성."""
    if frame.empty:
        return pd.DataFrame(), pd.DataFrame()

    amount_col = "net_revenue" if "net_revenue" in frame.columns else "amount"
    qty_col = "converted_quantity" if "converted_quantity" in frame.columns else "quantity"
    key_col = "option_product_label"
    df = frame.copy()
    if key_col not in df.columns:
        df[key_col] = [_option_product_label(a, b) for a, b in zip(df["product_name"], df["option_name"])]
    df["_d"] = df["date"].dt.date

    min_date = base_date - timedelta(days=59)
    scoped = df[(df["_d"] >= min_date) & (df["_d"] <= base_date)].copy()
    if scoped.empty:
        return pd.DataFrame(), pd.DataFrame()

    daily = (
        scoped.groupby([key_col, "_d"], as_index=False)
        .agg(
            qty=(qty_col, lambda s: float(pd.to_numeric(s, errors="coerce").fillna(0).sum())),
            rev=(amount_col, lambda s: float(pd.to_numeric(s, errors="coerce").fillna(0).sum())),
        )
    )

    options = sorted(daily[key_col].dropna().astype(str).unique().tolist())
    qty_rows: list[dict[str, object]] = []
    rev_rows: list[dict[str, object]] = []
    for opt in options:
        g = daily[daily[key_col] == opt].copy()
        qty_map = {row["_d"]: float(row["qty"]) for _, row in g.iterrows()}
        rev_map = {row["_d"]: float(row["rev"]) for _, row in g.iterrows()}

        # 기준일(오늘) + 이전 1~6일
        day_qty = [qty_map.get(base_date - timedelta(days=i), 0.0) for i in range(0, 7)]
        day_rev = [rev_map.get(base_date - timedelta(days=i), 0.0) for i in range(0, 7)]
        recent7_qty = float(sum(day_qty))
        recent7_rev = float(sum(day_rev))

        prev7_dates = [base_date - timedelta(days=i) for i in range(7, 14)]
        prev7_qty = float(sum(qty_map.get(d, 0.0) for d in prev7_dates))
        prev7_rev = float(sum(rev_map.get(d, 0.0) for d in prev7_dates))

        recent30_dates = [base_date - timedelta(days=i) for i in range(0, 30)]
        prev30_dates = [base_date - timedelta(days=i) for i in range(30, 60)]
        recent30_qty = float(sum(qty_map.get(d, 0.0) for d in recent30_dates))
        prev30_qty = float(sum(qty_map.get(d, 0.0) for d in prev30_dates))
        recent30_rev = float(sum(rev_map.get(d, 0.0) for d in recent30_dates))
        prev30_rev = float(sum(rev_map.get(d, 0.0) for d in prev30_dates))

        qty_week_diff = recent7_qty - prev7_qty
        qty_month_diff = recent30_qty - prev30_qty
        rev_week_diff = recent7_rev - prev7_rev
        rev_month_diff = recent30_rev - prev30_rev

        qty_week_pct = _safe_pct_change(recent7_qty, prev7_qty)
        qty_month_pct = _safe_pct_change(recent30_qty, prev30_qty)
        rev_week_pct = _safe_pct_change(recent7_rev, prev7_rev)
        rev_month_pct = _safe_pct_change(recent30_rev, prev30_rev)

        active_days = int(g[g["_d"].isin(recent30_dates)]["_d"].nunique())
        conf = _forecast_confidence_label(active_days)

        short_avg_qty = recent7_qty / 7.0
        med_avg_qty = recent30_qty / 30.0
        next7_qty = ((short_avg_qty + med_avg_qty) / 2.0) * 7.0

        short_avg_rev = recent7_rev / 7.0
        med_avg_rev = recent30_rev / 30.0
        next7_rev = ((short_avg_rev + med_avg_rev) / 2.0) * 7.0

        qty_rows.append(
            {
                key_col: opt,
                "base_day_qty": day_qty[0],
                "order_1_qty": day_qty[1],
                "order_2_qty": day_qty[2],
                "order_3_qty": day_qty[3],
                "order_4_qty": day_qty[4],
                "order_5_qty": day_qty[5],
                "order_6_qty": day_qty[6],
                "recent_7d_qty_sum": recent7_qty,
                "prev_7d_qty_sum": prev7_qty,
                "weekly_qty_diff": qty_week_diff,
                "weekly_qty_diff_pct": qty_week_pct,
                "recent_30d_qty_sum": recent30_qty,
                "prev_30d_qty_sum": prev30_qty,
                "monthly_qty_diff": qty_month_diff,
                "monthly_qty_diff_pct": qty_month_pct,
                "next_7d_qty_forecast": next7_qty,
                "forecast_confidence": conf,
            }
        )
        rev_rows.append(
            {
                key_col: opt,
                "base_day_rev": day_rev[0],
                "order_1_rev": day_rev[1],
                "order_2_rev": day_rev[2],
                "order_3_rev": day_rev[3],
                "order_4_rev": day_rev[4],
                "order_5_rev": day_rev[5],
                "order_6_rev": day_rev[6],
                "recent_7d_rev_sum": recent7_rev,
                "prev_7d_rev_sum": prev7_rev,
                "weekly_rev_diff": rev_week_diff,
                "weekly_rev_diff_pct": rev_week_pct,
                "recent_30d_rev_sum": recent30_rev,
                "prev_30d_rev_sum": prev30_rev,
                "monthly_rev_diff": rev_month_diff,
                "monthly_rev_diff_pct": rev_month_pct,
                "next_7d_rev_forecast": next7_rev,
                "forecast_confidence": conf,
            }
        )

    qty_df = pd.DataFrame(qty_rows).sort_values(
        ["weekly_qty_diff", "base_day_qty"], ascending=[False, False]
    )
    rev_df = pd.DataFrame(rev_rows).sort_values(
        ["weekly_rev_diff", "base_day_rev"], ascending=[False, False]
    )
    return qty_df, rev_df


def _append_totals_row(
    frame: pd.DataFrame,
    *,
    label_col: str = "option_product_label",
    label_text: str = "합계",
) -> pd.DataFrame:
    """숫자 컬럼 합계 하단 행 추가."""
    if frame.empty:
        return frame
    out = frame.copy()
    numeric_cols = [c for c in out.columns if c != label_col]
    total_data: dict[str, object] = {label_col: label_text}
    for col in numeric_cols:
        s = pd.to_numeric(out[col], errors="coerce")
        if s.notna().any():
            total_data[col] = float(s.fillna(0).sum())
        else:
            total_data[col] = ""
    total_row = pd.DataFrame([total_data])
    return pd.concat([out, total_row], ignore_index=True)


def _safe_date(value: object) -> date | None:
    ts = pd.to_datetime(value, errors="coerce")
    if pd.isna(ts):
        return None
    return ts.date()


def _build_happycall_candidates(
    frame: pd.DataFrame,
    report_date: date,
    *,
    lookback_days: int = 92,
) -> pd.DataFrame:
    """고객별 이탈 위험/해피콜 우선순위 후보를 계산한다."""
    from_date = report_date - timedelta(days=max(1, int(lookback_days)) - 1)
    scoped = frame[
        (frame["date"].dt.date >= from_date)
        & (frame["date"].dt.date <= report_date)
    ].copy()
    if scoped.empty:
        return pd.DataFrame()

    def _customer_composite_key(row: pd.Series) -> str:
        bid = str(row.get("buyer_id", "") or "").strip()
        bname = str(row.get("buyer_name", "") or "").strip()
        if bid and bname:
            return f"{bid}||{bname}"
        if bid:
            return f"{bid}||"
        if bname:
            return f"||{bname}"
        return ""

    scoped["_customer_key"] = scoped.apply(_customer_composite_key, axis=1)

    rows: list[dict[str, object]] = []
    for customer_key, grp in scoped.groupby("_customer_key"):
        if not str(customer_key or "").strip():
            continue
        first = grp.iloc[0]
        buyer = str(first.get("buyer_id", "") or "").strip()
        customer_name = str(first.get("buyer_name", "") or "").strip()
        g = grp.sort_values("date")
        order_days = sorted(
            {d for d in (_safe_date(v) for v in g["date"]) if d is not None}
        )
        if not order_days:
            continue
        last_order_date = order_days[-1]
        order_count = len(order_days)
        total_revenue = float(pd.to_numeric(g["net_revenue"], errors="coerce").fillna(0).sum())

        if order_count >= 2:
            cycles = [
                (order_days[i] - order_days[i - 1]).days
                for i in range(1, len(order_days))
                if (order_days[i] - order_days[i - 1]).days > 0
            ]
            avg_cycle_days = float(sum(cycles) / len(cycles)) if cycles else 7.0
        else:
            avg_cycle_days = 7.0

        expected_next = last_order_date + timedelta(days=max(1, int(round(avg_cycle_days))))
        delay_days = (report_date - expected_next).days
        if delay_days < 0:
            delay_days = 0

        if order_count == 1:
            segment = "신규"
        elif order_count <= 3:
            segment = "성장"
        elif delay_days >= max(2, int(round(avg_cycle_days * 0.5))):
            segment = "휴면위험"
        else:
            segment = "충성"

        score = min(delay_days * 10, 60) + min(order_count * 4, 20) + min(total_revenue / 100000, 20)
        if segment == "휴면위험":
            score += 10

        if segment == "신규":
            action = "첫 구매 만족도 확인 + 재구매 쿠폰 안내"
        elif segment == "휴면위험":
            action = "이탈 방지 해피콜 + 재구매 혜택 제안"
        elif segment == "성장":
            action = "주기 도래 전 리마인드 메시지/콜"
        else:
            action = "충성 고객 감사 혜택 안내"

        recent_dates = sorted(order_days, reverse=True)
        # 최근주문일은 별도 컬럼으로 두고, 주문①~⑤는 "직전 주문"부터 보여준다.
        recent_orders = recent_dates[1:6]
        previous_order = recent_dates[1] if len(recent_dates) >= 2 else None
        reorder_gap_days = (recent_dates[0] - recent_dates[1]).days if len(recent_dates) >= 2 else None
        option_rollup = (
            g.groupby("option_product_label", as_index=False)
            .agg(
                option_order_count=("order_id", lambda s: s.astype(str).replace("", pd.NA).dropna().nunique()),
                option_revenue=("net_revenue", lambda s: pd.to_numeric(s, errors="coerce").fillna(0).sum()),
            )
            .sort_values(["option_order_count", "option_revenue"], ascending=[False, False])
        )
        if option_rollup.empty:
            top_option = ""
            top_option_orders = 0
            top_option_revenue = 0.0
        else:
            top_row = option_rollup.iloc[0]
            top_option = str(top_row["option_product_label"] or "")
            top_option_orders = int(top_row["option_order_count"])
            top_option_revenue = float(top_row["option_revenue"])
        top_option_share_pct = (top_option_revenue / total_revenue * 100.0) if total_revenue > 0 else 0.0

        rows.append(
            {
                "buyer_id": buyer,
                "buyer_name": customer_name,
                "segment": segment,
                "order_count_lifetime": order_count,
                "revenue_lifetime": total_revenue,
                "last_order_date": last_order_date,
                "avg_cycle_days": avg_cycle_days,
                "expected_next_order_date": expected_next,
                "delay_days": delay_days,
                "priority_score": float(score),
                "recommended_action": action,
                "recent_order_date": recent_dates[0],
                "order_1_date": recent_orders[0] if len(recent_orders) >= 1 else None,
                "order_2_date": recent_orders[1] if len(recent_orders) >= 2 else None,
                "order_3_date": recent_orders[2] if len(recent_orders) >= 3 else None,
                "order_4_date": recent_orders[3] if len(recent_orders) >= 4 else None,
                "order_5_date": recent_orders[4] if len(recent_orders) >= 5 else None,
                "previous_order_date": previous_order,
                "reorder_days": reorder_gap_days,
                "top_option_product": _option_grid_display_text(top_option),
                "top_option_order_count": top_option_orders,
                "top_option_revenue": top_option_revenue,
                "top_option_revenue_share_pct": top_option_share_pct,
            }
        )
    if not rows:
        return pd.DataFrame()
    out = pd.DataFrame(rows).sort_values(["recent_order_date", "priority_score"], ascending=[False, False])
    return out


@st.cache_data(ttl=300)
def load_option_margin_snapshot(target_date: date) -> pd.DataFrame:
    """옵션별 마진 스냅샷 로드 (테이블 미생성 시 빈 결과)."""
    db = SessionLocal()
    try:
        rows = db.execute(
            text(
                """
                SELECT
                    stat_date,
                    product_name,
                    option_name,
                    delivery_fee_type,
                    order_count,
                    order_quantity,
                    net_revenue,
                    expected_settlement_amount,
                    customer_paid_shipping,
                    seller_shipping_burden,
                    estimated_cost,
                    margin_amount,
                    margin_rate_pct
                FROM agg_option_margin_daily
                WHERE stat_date = :d
                ORDER BY margin_amount DESC
                """
            ),
            {"d": target_date},
        ).mappings().all()
        return pd.DataFrame(rows)
    except Exception:
        return pd.DataFrame()
    finally:
        db.close()


@st.cache_data(ttl=120)
def load_option_cost_history() -> pd.DataFrame:
    """옵션 원가 이력 로드 (테이블 미생성 시 빈 결과)."""
    db = SessionLocal()
    try:
        rows = db.execute(
            text(
                """
                SELECT
                    id,
                    product_name,
                    COALESCE(option_name, '') AS option_name,
                    COALESCE(option_norm_key, '') AS option_norm_key,
                    option_code,
                    unit_cost,
                    pack_cost,
                    fulfillment_cost,
                    default_shipping_cost,
                    effective_from,
                    effective_to,
                    is_active,
                    note,
                    updated_at
                FROM product_option_cost_master
                WHERE is_active = 1
                ORDER BY product_name, option_name, effective_from DESC
                """
            )
        ).mappings().all()
        return pd.DataFrame(rows)
    except Exception:
        return pd.DataFrame()
    finally:
        db.close()


def ensure_option_norm_key_migration() -> tuple[bool, int]:
    """원가 테이블에 option_norm_key 컬럼/값을 보장한다. (컬럼생성여부, 백필건수)"""
    db = SessionLocal()
    added_col = False
    updated_rows = 0
    try:
        col_exists = db.execute(
            text(
                """
                SELECT COUNT(*) AS cnt
                FROM information_schema.columns
                WHERE table_schema = DATABASE()
                  AND table_name = 'product_option_cost_master'
                  AND column_name = 'option_norm_key'
                """
            )
        ).scalar()
        if int(col_exists or 0) == 0:
            db.execute(text("ALTER TABLE product_option_cost_master ADD COLUMN option_norm_key VARCHAR(120) NULL"))
            db.execute(
                text(
                    "CREATE INDEX idx_option_cost_norm_key ON product_option_cost_master (option_norm_key, effective_from)"
                )
            )
            added_col = True
            db.commit()
        rows = db.execute(
            text(
                """
                SELECT id, COALESCE(option_name, '') AS option_name
                FROM product_option_cost_master
                WHERE option_norm_key IS NULL OR option_norm_key = ''
                """
            )
        ).mappings().all()
        if rows:
            payload = [
                {"id": int(r["id"]), "option_norm_key": _option_norm_key(r["option_name"])}
                for r in rows
            ]
            db.execute(
                text("UPDATE product_option_cost_master SET option_norm_key = :option_norm_key WHERE id = :id"),
                payload,
            )
            updated_rows = len(payload)
            db.commit()
        return added_col, updated_rows
    finally:
        db.close()


def save_option_cost_history(
    *,
    product_name: str,
    option_name: str,
    effective_from: date,
    unit_cost: int,
    pack_cost: int = 0,
    fulfillment_cost: int = 0,
    note: str = "",
) -> str:
    """옵션 원가 이력 저장(동일 옵션/적용일 존재 시 갱신)."""
    db = SessionLocal()
    try:
        existing = db.execute(
            text(
                """
                SELECT 1
                FROM product_option_cost_master
                WHERE product_name = :product_name
                  AND option_name = :option_name
                  AND effective_from = :effective_from
                LIMIT 1
                """
            ),
            {
                "product_name": product_name.strip(),
                "option_name": option_name.strip(),
                "effective_from": effective_from,
            },
        ).first()
        db.execute(
            text(
                """
                INSERT INTO product_option_cost_master (
                    product_name,
                    option_name,
                    option_norm_key,
                    unit_cost,
                    pack_cost,
                    fulfillment_cost,
                    default_shipping_cost,
                    effective_from,
                    effective_to,
                    is_active,
                    note
                )
                VALUES (
                    :product_name,
                    :option_name,
                    :option_norm_key,
                    :unit_cost,
                    :pack_cost,
                    :fulfillment_cost,
                    0,
                    :effective_from,
                    NULL,
                    1,
                    :note
                )
                ON DUPLICATE KEY UPDATE
                    unit_cost = VALUES(unit_cost),
                    pack_cost = VALUES(pack_cost),
                    fulfillment_cost = VALUES(fulfillment_cost),
                    default_shipping_cost = VALUES(default_shipping_cost),
                    option_norm_key = VALUES(option_norm_key),
                    note = VALUES(note),
                    is_active = 1,
                    effective_to = NULL
                """
            ),
            {
                "product_name": product_name.strip(),
                "option_name": option_name.strip(),
                "option_norm_key": _option_norm_key(option_name),
                "effective_from": effective_from,
                "unit_cost": int(unit_cost),
                "pack_cost": int(pack_cost),
                "fulfillment_cost": int(fulfillment_cost),
                "note": note.strip(),
            },
        )
        db.commit()
        return "updated" if existing else "created"
    finally:
        db.close()


def seed_zero_option_cost_history(
    frame: pd.DataFrame,
    *,
    start_date: date,
    end_date: date,
    effective_from: date,
) -> tuple[int, int]:
    """조회 구간 주문 옵션을 0원가로 초기 생성한다. (신규 건수, 기존 건수)"""
    if frame.empty:
        return 0, 0
    scoped = frame[(frame["date"].dt.date >= start_date) & (frame["date"].dt.date <= end_date)].copy()
    if scoped.empty:
        return 0, 0
    options = (
        scoped.assign(option_name_norm=scoped["option_name"].fillna("").astype(str))
        .assign(option_norm_key=lambda d: d["option_name_norm"].map(_option_norm_key))
        .groupby(["product_name", "option_name_norm", "option_norm_key"], as_index=False)
        .size()
    )
    if options.empty:
        return 0, 0
    norm_candidates: dict[str, str] = {}
    for _, row in options.iterrows():
        option_name = str(row["option_name_norm"]).strip()
        norm_key = _option_norm_key(option_name)
        if norm_key not in norm_candidates:
            norm_candidates[norm_key] = option_name
    db = SessionLocal()
    try:
        existing_rows = db.execute(
            text(
                """
                SELECT COALESCE(option_norm_key, '') AS option_norm_key
                FROM product_option_cost_master
                WHERE effective_from = :effective_from
                """
            ),
            {"effective_from": effective_from},
        ).mappings().all()
        existing_keys = {str(r["option_norm_key"]).strip() for r in existing_rows}
        new_norm_keys = [k for k in norm_candidates.keys() if k not in existing_keys]
        if not new_norm_keys:
            return 0, len(norm_candidates)
        db.execute(
            text(
                """
                INSERT INTO product_option_cost_master (
                    product_name,
                    option_name,
                    option_norm_key,
                    unit_cost,
                    pack_cost,
                    fulfillment_cost,
                    default_shipping_cost,
                    effective_from,
                    effective_to,
                    is_active,
                    note
                )
                VALUES (
                    :product_name,
                    :option_name,
                    :option_norm_key,
                    0,
                    0,
                    0,
                    0,
                    :effective_from,
                    NULL,
                    1,
                    :note
                )
                ON DUPLICATE KEY UPDATE
                    note = note
                """
            ),
            [
                {
                    "product_name": "__OPTION_NORM__",
                    "option_name": norm_candidates[norm_key],
                    "option_norm_key": norm_key,
                    "effective_from": effective_from,
                    "note": "초기생성(0원가)",
                }
                for norm_key in new_norm_keys
            ],
        )
        db.commit()
        return len(new_norm_keys), len(norm_candidates) - len(new_norm_keys)
    finally:
        db.close()


def _effective_cost_row(
    costs: pd.DataFrame,
    *,
    product_name: str,
    option_name: str,
    stat_date: date,
) -> pd.Series | None:
    if costs.empty:
        return None
    stat_ts = pd.Timestamp(stat_date)
    norm_key = _option_norm_key(option_name)
    c = costs[costs["option_norm_key"].astype(str) == str(norm_key)].copy()
    if c.empty:
        # fallback: 마이그레이션 전/예외 케이스 호환
        c = costs[costs["option_name"].astype(str) == str(option_name)].copy()
    if c.empty:
        return None
    c["effective_from"] = pd.to_datetime(c["effective_from"], errors="coerce")
    c["effective_to"] = pd.to_datetime(c["effective_to"], errors="coerce")
    c = c[
        c["effective_from"].notna()
        & (c["effective_from"] <= stat_ts)
        & ((c["effective_to"].isna()) | (c["effective_to"] >= stat_ts))
    ]
    if c.empty:
        return None
    c = c.sort_values(["effective_from", "updated_at"], ascending=[False, False])
    return c.iloc[0]


def _build_missing_option_queue(
    frame: pd.DataFrame,
    costs: pd.DataFrame,
    *,
    as_of_date: date,
) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    o = frame.copy()
    o = o[o["date"].dt.date <= as_of_date]
    if o.empty:
        return pd.DataFrame()
    if "option_product_label" not in o.columns:
        o["option_product_label"] = [_option_product_label(a, b) for a, b in zip(o["product_name"], o["option_name"])]
    o["option_name_norm"] = o["option_name"].fillna("").astype(str)
    options = (
        o.assign(option_norm_key=o["option_name_norm"].map(_option_norm_key))
        .groupby(["option_name_norm", "option_norm_key"], as_index=False)
        .agg(
            recent_order_date=("date", lambda s: pd.to_datetime(s, errors="coerce").max()),
            recent_7d_revenue=(
                "net_revenue",
                lambda s: pd.to_numeric(s, errors="coerce").fillna(0).sum(),
            ),
        )
    )
    if not costs.empty:
        c = costs.copy()
        c["effective_from"] = pd.to_datetime(c["effective_from"], errors="coerce").dt.date
        covered = c[c["effective_from"].notna() & (c["effective_from"] <= as_of_date)][
            ["option_norm_key"]
        ].drop_duplicates()
        covered["covered"] = 1
        options = options.merge(
            covered,
            left_on=["option_norm_key"],
            right_on=["option_norm_key"],
            how="left",
        )
    else:
        options["covered"] = pd.NA
    options["cost_apply_status"] = options["covered"].map(lambda x: "원가입력완료" if x == 1 else "원가미입력")
    missing = options[options["cost_apply_status"] == "원가미입력"].copy()
    if missing.empty:
        return missing
    missing["recent_order_date"] = pd.to_datetime(missing["recent_order_date"], errors="coerce").dt.date
    missing["option_name_norm"] = missing["option_name_norm"].map(_option_name_display)
    return missing[
        ["option_name_norm", "recent_order_date", "recent_7d_revenue", "cost_apply_status"]
    ].rename(columns={"option_name_norm": "option_name"}).sort_values(
        ["recent_7d_revenue", "recent_order_date"], ascending=[False, False]
    )


def _build_margin_result_view(
    frame: pd.DataFrame,
    costs: pd.DataFrame,
    *,
    start_date: date,
    end_date: date,
) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    o = frame[(frame["date"].dt.date >= start_date) & (frame["date"].dt.date <= end_date)].copy()
    if o.empty:
        return pd.DataFrame()
    o["option_name_norm"] = o["option_name"].fillna("").astype(str)
    o["option_norm_key"] = o["option_name_norm"].map(_option_norm_key)
    o["stat_date"] = o["date"].dt.date
    o["order_quantity_num"] = pd.to_numeric(o["quantity"], errors="coerce").fillna(0)
    o["net_revenue_num"] = pd.to_numeric(o["net_revenue"], errors="coerce").fillna(0)
    day_rows = (
        o.groupby(
            ["stat_date", "option_name_norm", "option_norm_key"],
            as_index=False,
        )
        .agg(
            order_quantity=("order_quantity_num", "sum"),
            net_revenue=("net_revenue_num", "sum"),
            order_count=("order_id", lambda s: s.astype(str).replace("", pd.NA).dropna().nunique()),
        )
        .sort_values(["option_norm_key", "stat_date"])
    )
    cost_rows: list[dict[str, object]] = []
    for _, row in day_rows.iterrows():
        stat_date = row["stat_date"]
        option_name = str(row["option_name_norm"])
        cost_row = _effective_cost_row(
            costs, product_name="__OPTION_NORM__", option_name=option_name, stat_date=stat_date
        )
        if cost_row is None:
            unit_cost = 0.0
            pack_cost = 0.0
            fulfillment_cost = 0.0
            effective_from = pd.NaT
            applied = False
        else:
            unit_cost = float(pd.to_numeric(cost_row.get("unit_cost"), errors="coerce") or 0.0)
            pack_cost = float(pd.to_numeric(cost_row.get("pack_cost"), errors="coerce") or 0.0)
            fulfillment_cost = float(pd.to_numeric(cost_row.get("fulfillment_cost"), errors="coerce") or 0.0)
            effective_from = pd.to_datetime(cost_row.get("effective_from"), errors="coerce")
            applied = True
        total_unit_cost = unit_cost + pack_cost + fulfillment_cost
        estimated_cost = float(row["order_quantity"]) * total_unit_cost
        margin_amount = float(row["net_revenue"]) - estimated_cost
        cost_rows.append(
            {
                "stat_date": stat_date,
                "option_norm_key": row["option_norm_key"],
                "option_name": _option_name_display(option_name),
                "net_revenue": float(row["net_revenue"]),
                "order_quantity": float(row["order_quantity"]),
                "order_count": int(row["order_count"]),
                "estimated_cost": estimated_cost,
                "margin_amount": margin_amount,
                "cost_applied": applied,
                "applied_unit_cost": total_unit_cost,
                "latest_effective_from": effective_from,
            }
        )
    calc = pd.DataFrame(cost_rows)
    if calc.empty:
        return calc
    out = (
        calc.groupby("option_norm_key", as_index=False)
        .agg(
            option_name=("option_name", "first"),
            net_revenue=("net_revenue", "sum"),
            order_quantity=("order_quantity", "sum"),
            order_count=("order_count", "sum"),
            estimated_cost=("estimated_cost", "sum"),
            margin_amount=("margin_amount", "sum"),
            missing_cost_days=("cost_applied", lambda s: int((~pd.Series(s).fillna(False)).sum())),
            latest_effective_from=("latest_effective_from", "max"),
            applied_unit_cost=("applied_unit_cost", "max"),
        )
    )
    out["margin_rate_pct"] = out.apply(
        lambda r: (float(r["margin_amount"]) / float(r["net_revenue"]) * 100.0) if float(r["net_revenue"]) > 0 else 0.0,
        axis=1,
    )
    out["cost_apply_status"] = out["missing_cost_days"].map(
        lambda v: "원가적용완료" if int(v) == 0 else ("원가없음" if int(v) > 0 else "확인필요")
    )
    out = out.sort_values(["margin_rate_pct", "net_revenue"], ascending=[True, False])
    return out


st.set_page_config(page_title="네이버 모디바 대시보드", layout="wide")


def _read_secret_or_env(key: str) -> str | None:
    """Streamlit secrets 우선, 없으면 환경변수에서 읽는다."""
    try:
        value = st.secrets.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    except Exception:
        pass
    env_value = os.environ.get(key)
    if isinstance(env_value, str) and env_value.strip():
        return env_value.strip()
    return None


def _verify_credentials(username: str, password: str) -> bool:
    """아이디/비밀번호를 안전하게 비교한다."""
    expected_username = _read_secret_or_env("DASHBOARD_USERNAME")
    expected_password = _read_secret_or_env("DASHBOARD_PASSWORD")

    if expected_username is None:
        expected_username = "admin"

    if expected_password is None:
        return False

    username_ok = hmac.compare_digest(username.strip(), expected_username)
    password_ok = hmac.compare_digest(password, expected_password)
    return username_ok and password_ok


def _require_login() -> bool:
    if st.session_state.get("authenticated", False):
        return True

    st.title("🔐 보안 접속")
    with st.form("login_form", clear_on_submit=False):
        username = st.text_input("아이디")
        password = st.text_input("비밀번호", type="password")
        submit = st.form_submit_button("로그인", type="primary")
    if submit:
        if _verify_credentials(username=username, password=password):
            st.session_state.authenticated = True
            st.session_state["auth_user"] = username.strip()
            st.session_state["_refresh_orders_after_login"] = True
            fetch_order_data.clear()
            fetch_db_stats.clear()
            st.rerun()
        else:
            st.error("아이디 또는 비밀번호가 올바르지 않습니다.")

    st.stop()
    return False


def main_content() -> None:
    if st.session_state.pop("_refresh_orders_after_login", False):
        fetch_order_data.clear()
        fetch_db_stats.clear()

    _safe_autorefresh(interval_ms=60000, key="naver_modiba_dashboard_autorefresh")

    apply_dashboard_theme()

    api_base_url = DEFAULT_API_BASE_URL
    revenue_basis = "payment"
    db_subtitle = ""
    try:
        ds = fetch_db_stats(api_base_url)
        _mark_api_success()
        lp = ds.get("latest_payment_date")
        lb = ds.get("latest_business_date")
        db_subtitle = (
            f"DB `orders` {int(ds.get('orders_count') or 0):,}건 · "
            f"최신 결제일시 {lp or '—'} · "
            f"최신 영업일(집계 `date`){lb or '—'}"
        )
    except Exception as exc:
        _mark_api_failure(exc)
        db_subtitle = "DB 통계(`/analytics/db-stats`)를 불러오지 못했습니다."

    render_page_title(
        "네이버 친절한 모디바 주문현황",
        subtitle=db_subtitle,
    )
    _render_api_health_caption()

    anchor = _kst_anchor_business_date()
    fetch_start = anchor - timedelta(days=ORDERS_RAW_FETCH_DAYS)

    data_loaded_at = ""
    data_loaded_mode = "live"
    try:
        raw_df = fetch_order_data(api_base_url, revenue_basis, fetch_start, anchor)
        order_df = normalize_order_data(raw_df)
        st.session_state["last_good_order_df"] = order_df.copy()
        data_loaded_at = format_now_kst()
        st.session_state["last_data_loaded_at"] = data_loaded_at
        st.session_state["last_data_loaded_mode"] = "live"
        _mark_api_success()
    except Exception as exc:
        _mark_api_failure(exc)
        cached_df = st.session_state.get("last_good_order_df")
        if isinstance(cached_df, pd.DataFrame) and not cached_df.empty:
            order_df = cached_df.copy()
            data_loaded_at = st.session_state.get("last_data_loaded_at", "")
            data_loaded_mode = "fallback"
        else:
            st.error("주문 데이터를 불러오지 못했습니다. 사이드바 API URL을 확인하세요.")
            st.stop()

    if not data_loaded_at:
        data_loaded_at = st.session_state.get("last_data_loaded_at", "")

    if order_df.empty:
        st.stop()

    if "option_product_label" not in order_df.columns:
        order_df = order_df.copy()
        order_df["option_product_label"] = [
            _option_product_label(a, b) for a, b in zip(order_df["product_name"], order_df["option_name"])
        ]

    if data_loaded_mode == "fallback":
        st.warning("네트워크/API 오류로 캐시된 마지막 정상 데이터를 표시 중입니다.")

    now_kst = datetime.now(KST)
    default_end = anchor
    default_start = default_end

    tab_kpi, tab_summary, tab_customer, tab_product, tab_margin, tab_detail = st.tabs(
        ["KPI", "요약", "고객", "상품", "마진", "분석상세"]
    )

    report_date = default_end
    compare_date = report_date - timedelta(days=7)
    happycall_df = _build_happycall_candidates(order_df, report_date)

    def _delta_text(curr: float, prev: float) -> str:
        if prev == 0:
            return "0.0%"
        return f"{((curr - prev) / prev) * 100.0:.1f}%"

    with tab_summary:
        section_heading("경영 요약 리포트")
        available_dates = sorted({d for d in order_df["date"].dt.date.dropna().unique()})
        if not available_dates:
            st.caption("요약 기준일로 선택할 주문 데이터가 없습니다.")
            st.stop()
        summary_report_date = st.date_input(
            "요약 기준일",
            value=available_dates[-1],
            min_value=available_dates[0],
            max_value=available_dates[-1],
            key="summary_report_date",
            help="선택한 날짜의 당일 매출을 기준으로, 전주 동일요일(7일 전 하루)과 비교합니다.",
        )
        summary_compare_date = summary_report_date - timedelta(days=7)
        report_summary = _daily_summary_from_orders(order_df, summary_report_date)
        compare_summary = _daily_summary_from_orders(order_df, summary_compare_date)
        forecast_amount, forecast_conf = _simple_nextday_forecast(order_df, summary_report_date)
        product_delta = _product_revenue_delta_table(order_df, summary_report_date, summary_compare_date)

        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric(
            "순매출",
            f"{report_summary['total_amount']:,.0f}원",
            _delta_text(report_summary["total_amount"], compare_summary["total_amount"]),
        )
        m2.metric(
            "주문건수",
            f"{int(report_summary['order_count']):,}",
            _delta_text(report_summary["order_count"], compare_summary["order_count"]),
        )
        m3.metric(
            "주문수량",
            f"{report_summary['order_quantity']:,.0f}",
            _delta_text(report_summary["order_quantity"], compare_summary["order_quantity"]),
        )
        m4.metric(
            "판매수량",
            f"{report_summary['sold_quantity']:,.0f}",
            _delta_text(report_summary["sold_quantity"], compare_summary["sold_quantity"]),
        )
        m5.metric(
            "고객수",
            f"{int(report_summary['customer_count']):,}",
            _delta_text(report_summary["customer_count"], compare_summary["customer_count"]),
        )
        st.caption(
            f"기준일 {summary_report_date} · 전주 비교일 {summary_compare_date} · "
            f"내일 예상 매출 {forecast_amount:,.0f}원 (신뢰도 {forecast_conf})"
        )

        rise_df = product_delta.head(5)[
            [
                "option_product_label",
                "current_revenue",
                "prev_revenue",
                "revenue_diff",
                "revenue_diff_pct",
            ]
        ].copy()
        rise_df["option_product_label"] = rise_df["option_product_label"].map(_option_grid_display_text)
        rise_df = _append_summary_delta_total_row(rise_df)
        fall_df = product_delta.sort_values("revenue_diff").head(5)[
            [
                "option_product_label",
                "current_revenue",
                "prev_revenue",
                "revenue_diff",
                "revenue_diff_pct",
            ]
        ].copy()
        fall_df["option_product_label"] = fall_df["option_product_label"].map(_option_grid_display_text)
        fall_df = _append_summary_delta_total_row(fall_df)

        title_l, title_r = st.columns(2)
        with title_l:
            st.markdown("#### 전주 대비 상승 옵션 Top5")
        with title_r:
            st.markdown("#### 전주 대비 하락 옵션 Top5")
        rise_col, fall_col = st.columns(2)
        with rise_col:
            show_data_grid(
                rise_df,
                keep_input_order=True,
                height=SUMMARY_RISE_FALL_DATAFRAME_HEIGHT_PX,
            )
        with fall_col:
            show_data_grid(
                fall_df,
                keep_input_order=True,
                height=SUMMARY_RISE_FALL_DATAFRAME_HEIGHT_PX,
            )

        action_msgs: list[str] = []
        if report_summary["total_amount"] < compare_summary["total_amount"]:
            action_msgs.append("순매출이 전주 대비 하락: 하락 Top 옵션의 가격·노출·재고를 우선 점검하세요.")
        if report_summary["customer_count"] < compare_summary["customer_count"]:
            action_msgs.append("고객수가 감소: 최근 2주 미주문 고객 리마인드(해피콜/메시지) 캠페인을 진행하세요.")
        if forecast_amount < report_summary["total_amount"]:
            action_msgs.append("내일 예상 매출이 오늘보다 낮음: 상위 상품 재구매 유도 번들/쿠폰을 사전 노출하세요.")
        if not action_msgs:
            action_msgs.append("주요 지표가 안정적입니다. 상승 상품 재고/배송 품질 유지에 집중하세요.")
        st.info("오늘 실행 액션: " + " / ".join(action_msgs[:3]))

    with tab_product:
        section_heading("상품 옵션 추이(수량/매출)", level=3)
        available_dates = sorted({d for d in order_df["date"].dt.date.dropna().unique()})
        if not available_dates:
            st.caption("상품 추이 분석에 사용할 날짜 데이터가 없습니다.")
        else:
            product_base_date = st.date_input(
                "상품 분석 기준일",
                value=available_dates[-1],
                min_value=available_dates[0],
                max_value=available_dates[-1],
                key="product_base_date",
                help="선택한 날짜를 기준으로 일/주/월 수량·매출 추이를 계산합니다.",
            )
            qty_trend, rev_trend = _build_option_trend_snapshot(order_df, product_base_date)
            if qty_trend.empty or rev_trend.empty:
                st.caption("선택한 기준일에 분석 가능한 옵션 데이터가 없습니다.")
            else:
                qty_trend = qty_trend.sort_values(
                    ["base_day_qty", "weekly_qty_diff"],
                    ascending=[False, False],
                )
                base_qty_for_sort = qty_trend[["option_product_label", "base_day_qty"]].copy()
                rev_trend = rev_trend.merge(base_qty_for_sort, on="option_product_label", how="left")
                rev_trend["base_day_qty"] = pd.to_numeric(
                    rev_trend["base_day_qty"], errors="coerce"
                ).fillna(0.0)
                rev_trend = rev_trend.sort_values(
                    ["base_day_qty", "base_day_rev", "weekly_rev_diff"],
                    ascending=[False, False, False],
                )
                qty_cols = [
                    "option_product_label",
                    "base_day_qty",
                    "order_1_qty",
                    "order_2_qty",
                    "order_3_qty",
                    "order_4_qty",
                    "order_5_qty",
                    "order_6_qty",
                    "recent_7d_qty_sum",
                    "prev_7d_qty_sum",
                    "weekly_qty_diff",
                    "weekly_qty_diff_pct",
                    "recent_30d_qty_sum",
                    "prev_30d_qty_sum",
                    "monthly_qty_diff",
                    "monthly_qty_diff_pct",
                    "next_7d_qty_forecast",
                    "forecast_confidence",
                ]
                rev_cols = [
                    "option_product_label",
                    "base_day_rev",
                    "order_1_rev",
                    "order_2_rev",
                    "order_3_rev",
                    "order_4_rev",
                    "order_5_rev",
                    "order_6_rev",
                    "recent_7d_rev_sum",
                    "prev_7d_rev_sum",
                    "weekly_rev_diff",
                    "weekly_rev_diff_pct",
                    "recent_30d_rev_sum",
                    "prev_30d_rev_sum",
                    "monthly_rev_diff",
                    "monthly_rev_diff_pct",
                    "next_7d_rev_forecast",
                    "forecast_confidence",
                ]
                display_mode = st.radio(
                    "표시 기준",
                    options=["수량", "금액", "수량+금액"],
                    horizontal=True,
                    key="product_trend_display_mode",
                )
                st.caption(
                    f"기준일 {product_base_date} · 주문①~⑥은 기준일 이전 1~6일 · "
                    "수량은 환산수량(주문수량×내품수량) 기준 · "
                    "주/월 비교는 최근 구간 합계 vs 직전 동일 길이 구간 합계입니다."
                )
                day_labels_qty = {
                    "base_day_qty": f"{product_base_date.day}일 수량",
                    "order_1_qty": f"{(product_base_date - timedelta(days=1)).day}일 수량",
                    "order_2_qty": f"{(product_base_date - timedelta(days=2)).day}일 수량",
                    "order_3_qty": f"{(product_base_date - timedelta(days=3)).day}일 수량",
                    "order_4_qty": f"{(product_base_date - timedelta(days=4)).day}일 수량",
                    "order_5_qty": f"{(product_base_date - timedelta(days=5)).day}일 수량",
                    "order_6_qty": f"{(product_base_date - timedelta(days=6)).day}일 수량",
                }
                day_labels_rev = {
                    "base_day_rev": f"{product_base_date.day}일 매출",
                    "order_1_rev": f"{(product_base_date - timedelta(days=1)).day}일 매출",
                    "order_2_rev": f"{(product_base_date - timedelta(days=2)).day}일 매출",
                    "order_3_rev": f"{(product_base_date - timedelta(days=3)).day}일 매출",
                    "order_4_rev": f"{(product_base_date - timedelta(days=4)).day}일 매출",
                    "order_5_rev": f"{(product_base_date - timedelta(days=5)).day}일 매출",
                    "order_6_rev": f"{(product_base_date - timedelta(days=6)).day}일 매출",
                }
                if display_mode in ("수량", "수량+금액"):
                    qty_show = qty_trend[qty_cols].head(30).copy()
                    qty_show["option_product_label"] = qty_show["option_product_label"].map(
                        _option_grid_display_text
                    )
                    st.markdown("#### 수량 추이 - 일자")
                    qty_daily_cols = [
                        "option_product_label",
                        "base_day_qty",
                        "order_1_qty",
                        "order_2_qty",
                        "order_3_qty",
                        "order_4_qty",
                        "order_5_qty",
                        "order_6_qty",
                    ]
                    qty_daily = qty_show[qty_daily_cols].rename(columns=day_labels_qty)
                    day_sum_cols = [c for c in qty_daily.columns if c != "option_product_label"]
                    qty_daily["일자 합계"] = pd.to_numeric(
                        qty_daily[day_sum_cols].sum(axis=1),
                        errors="coerce",
                    ).fillna(0.0)
                    qty_daily = _append_totals_row(qty_daily)
                    show_data_grid(qty_daily, keep_input_order=True)

                    st.markdown("#### 수량 추이 - 주간")
                    qty_week_cols = [
                        "option_product_label",
                        "recent_7d_qty_sum",
                        "prev_7d_qty_sum",
                        "weekly_qty_diff",
                        "weekly_qty_diff_pct",
                    ]
                    qty_week = _append_totals_row(qty_show[qty_week_cols])
                    if not qty_week.empty:
                        qty_week.at[len(qty_week) - 1, "weekly_qty_diff_pct"] = float("nan")
                    show_data_grid(qty_week, keep_input_order=True)

                    st.markdown("#### 수량 추이 - 월간/예측")
                    qty_month_cols = [
                        "option_product_label",
                        "recent_30d_qty_sum",
                        "prev_30d_qty_sum",
                        "monthly_qty_diff",
                        "monthly_qty_diff_pct",
                        "next_7d_qty_forecast",
                        "forecast_confidence",
                    ]
                    qty_month = qty_show[qty_month_cols].copy()
                    qty_month = _append_totals_row(qty_month)
                    if not qty_month.empty:
                        qty_month.at[len(qty_month) - 1, "monthly_qty_diff_pct"] = float("nan")
                        qty_month.at[len(qty_month) - 1, "forecast_confidence"] = ""
                    show_data_grid(qty_month, keep_input_order=True)
                if display_mode in ("금액", "수량+금액"):
                    rev_show = rev_trend[rev_cols].head(30).copy()
                    rev_show["option_product_label"] = rev_show["option_product_label"].map(
                        _option_grid_display_text
                    )
                    st.markdown("#### 금액 추이 - 일자")
                    rev_daily_cols = [
                        "option_product_label",
                        "base_day_rev",
                        "order_1_rev",
                        "order_2_rev",
                        "order_3_rev",
                        "order_4_rev",
                        "order_5_rev",
                        "order_6_rev",
                    ]
                    rev_daily = rev_show[rev_daily_cols].rename(columns=day_labels_rev)
                    show_data_grid(rev_daily, keep_input_order=True)

                    st.markdown("#### 금액 추이 - 주간")
                    rev_week_cols = [
                        "option_product_label",
                        "recent_7d_rev_sum",
                        "prev_7d_rev_sum",
                        "weekly_rev_diff",
                        "weekly_rev_diff_pct",
                    ]
                    show_data_grid(rev_show[rev_week_cols], keep_input_order=True)

                    st.markdown("#### 금액 추이 - 월간/예측")
                    rev_month_cols = [
                        "option_product_label",
                        "recent_30d_rev_sum",
                        "prev_30d_rev_sum",
                        "monthly_rev_diff",
                        "monthly_rev_diff_pct",
                        "next_7d_rev_forecast",
                        "forecast_confidence",
                    ]
                    show_data_grid(rev_show[rev_month_cols], keep_input_order=True)

    with tab_customer:
        section_heading("고객 이탈/해피콜 실행판", level=3)
        if happycall_df.empty:
            st.caption("해피콜 대상 계산을 위한 고객 데이터가 없습니다.")
        else:
            total_candidates = len(happycall_df)
            risk_count = int((happycall_df["segment"] == "휴면위험").sum())
            avg_delay = float(pd.to_numeric(happycall_df["delay_days"], errors="coerce").fillna(0).mean())
            top_revenue_target = float(
                pd.to_numeric(happycall_df.head(20)["revenue_lifetime"], errors="coerce").fillna(0).sum()
            )

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("오늘 콜 대상(추천)", f"{total_candidates:,}")
            c2.metric("휴면위험 고객", f"{risk_count:,}")
            c3.metric("평균 지연일", f"{avg_delay:.1f}일")
            c4.metric("우선대상 예상 LTV", f"{top_revenue_target:,.0f}원")

            available_recent_dates = sorted({d for d in happycall_df["recent_order_date"].dropna().tolist()})
            if not available_recent_dates:
                st.caption("최근주문일 기준으로 표시할 데이터가 없습니다.")
                st.stop()
            customer_base_date = st.date_input(
                "고객 조회 기준일",
                value=available_recent_dates[-1],
                min_value=available_recent_dates[0],
                max_value=available_recent_dates[-1],
                key="customer_recent_base_date",
                help="선택한 기준일 + 전일(2일) 구간의 최근주문 고객을 표시합니다.",
            )
            customer_window_start = customer_base_date - timedelta(days=1)
            st.caption(
                f"최근 3개월 주문 기준 · 최근주문일 {customer_window_start}~{customer_base_date} "
                "필터 · 재주문기간(일) 짧은순 정렬"
            )
            action_choices = sorted(
                happycall_df["recommended_action"].dropna().astype(str).unique().tolist()
            )
            selected_actions = st.multiselect(
                "권장 액션 필터",
                options=action_choices,
                default=[],
                key="happycall_recommended_action_filter",
                help="선택한 권장 액션만 표에 표시합니다. 선택이 없으면 전체입니다.",
            )
            display_happycall = (
                happycall_df
                if not selected_actions
                else happycall_df[happycall_df["recommended_action"].isin(selected_actions)]
            )
            display_happycall = display_happycall[
                (display_happycall["recent_order_date"] >= customer_window_start)
                & (display_happycall["recent_order_date"] <= customer_base_date)
            ]
            display_happycall = display_happycall.copy()
            display_happycall["_reorder_sort"] = (
                pd.to_numeric(display_happycall["reorder_days"], errors="coerce")
                .fillna(999999)
                .astype(float)
            )
            display_happycall = display_happycall.sort_values(
                ["_reorder_sort", "recent_order_date", "priority_score"],
                ascending=[True, False, False],
            )
            call_cols = [
                "buyer_id",
                "buyer_name",
                "recent_order_date",
                "order_1_date",
                "order_2_date",
                "order_3_date",
                "order_4_date",
                "order_5_date",
                "previous_order_date",
                "reorder_days",
                "top_option_product",
                "top_option_order_count",
                "top_option_revenue",
                "top_option_revenue_share_pct",
                "segment",
                "last_order_date",
                "expected_next_order_date",
                "delay_days",
                "order_count_lifetime",
                "revenue_lifetime",
                "priority_score",
                "recommended_action",
            ]
            if display_happycall.empty:
                st.caption("선택한 권장 액션에 해당하는 행이 없습니다. 필터를 해제하세요.")
            else:
                display_happycall_show = display_happycall[call_cols].head(30).copy()
                compact_date_cols = [
                    "recent_order_date",
                    "order_1_date",
                    "order_2_date",
                    "order_3_date",
                    "order_4_date",
                    "order_5_date",
                    "previous_order_date",
                ]
                for col in compact_date_cols:
                    display_happycall_show[col] = display_happycall_show[col].map(_format_month_day)
                show_data_grid(display_happycall_show, keep_input_order=True)

    with tab_margin:
        section_heading("가격-매출-마진 방어판", level=3)
        migration_msg = ""
        try:
            added_col, updated_rows = ensure_option_norm_key_migration()
            if added_col:
                migration_msg = "option_norm_key 컬럼을 생성했습니다."
            if updated_rows > 0:
                migration_msg = (migration_msg + " " if migration_msg else "") + f"기존 원가 {updated_rows}건을 정규화했습니다."
        except Exception as exc:
            st.warning(f"원가 정규화 마이그레이션 확인 중 오류: {exc}")
        cost_history_df = load_option_cost_history()
        if migration_msg:
            st.info(migration_msg)
        margin_base_col, margin_period_col = st.columns([2, 2])
        with margin_base_col:
            margin_base_date = st.date_input(
                "마진 조회 기준일",
                value=report_date,
                min_value=available_dates[0] if available_dates else report_date,
                max_value=available_dates[-1] if available_dates else report_date,
                key="margin_base_date",
            )
        with margin_period_col:
            margin_window_days = st.selectbox(
                "조회 기간",
                options=[7, 30],
                index=1,
                key="margin_window_days",
                format_func=lambda d: f"최근 {d}일",
            )
        margin_start_date = margin_base_date - timedelta(days=int(margin_window_days) - 1)
        st.caption(
            f"주문 발생 옵션 기준 원가 관리 · 조회구간 {margin_start_date}~{margin_base_date} "
            "(주문일 시점 유효 원가 적용)"
        )
        init_col1, init_col2 = st.columns([2, 6])
        with init_col1:
            bootstrap_submitted = st.button(
                "조회구간 옵션 0원가 초기생성",
                key="margin_zero_cost_bootstrap_btn",
                help="현재 조회구간 주문 옵션 중 원가 마스터에 없는 항목을 0원가로 초기 등록합니다.",
            )
        with init_col2:
            st.caption(
                "초기값은 unit/pack/fulfillment 모두 0으로 저장됩니다. "
                "생성 후 아래 원가 입력 위젯에서 실제 원가로 수정하세요."
            )
        if bootstrap_submitted:
            try:
                inserted_count, existing_count = seed_zero_option_cost_history(
                    order_df,
                    start_date=margin_start_date,
                    end_date=margin_base_date,
                    effective_from=margin_base_date,
                )
                load_option_cost_history.clear()
                if inserted_count > 0:
                    st.success(
                        f"초기 0원가 데이터 {inserted_count}건을 생성했습니다. "
                        f"(기존 {existing_count}건)"
                    )
                else:
                    st.info("조회구간 옵션은 이미 초기 데이터가 준비되어 있습니다.")
                st.rerun()
            except Exception as exc:
                st.error(f"초기 데이터 생성 중 오류가 발생했습니다: {exc}")

        section_heading("원가 미입력 옵션 큐", level=3)
        missing_queue_df = _build_missing_option_queue(order_df, cost_history_df, as_of_date=margin_base_date)
        if missing_queue_df.empty:
            st.caption("미입력 옵션이 없습니다. 새로 주문된 옵션도 자동으로 이 목록에 나타납니다.")
        else:
            missing_show = missing_queue_df.head(50).copy()
            missing_show["option_name"] = missing_show["option_name"].map(_option_name_display)
            missing_show = missing_show[
                ["option_name", "recent_order_date", "recent_7d_revenue", "cost_apply_status"]
            ]
            show_data_grid(missing_show, keep_input_order=True)

        section_heading("옵션 원가 입력", level=3)
        option_master = (
            order_df.assign(option_name_norm=order_df["option_name"].fillna("").astype(str))
            .assign(option_norm_key=lambda d: d["option_name_norm"].map(_option_norm_key))
            .groupby(["option_norm_key", "option_name_norm"], as_index=False)
            .agg(recent_order_date=("date", lambda s: pd.to_datetime(s, errors="coerce").max()))
            .sort_values("recent_order_date", ascending=False)
        )
        option_master["option_name_display"] = option_master["option_name_norm"].map(_option_name_display)
        option_choices = option_master["option_norm_key"].dropna().astype(str).unique().tolist()
        if not option_choices:
            st.caption("원가 입력 대상 옵션이 없습니다.")
        else:
            default_option = option_choices[0]
            if not missing_queue_df.empty:
                default_option = _option_norm_key(str(missing_queue_df.iloc[0]["option_name"]))
            with st.form("margin_cost_input_form", clear_on_submit=False):
                selected_option_label = st.selectbox(
                    "옵션상품명",
                    options=option_choices,
                    index=max(0, option_choices.index(default_option)) if default_option in option_choices else 0,
                    key="margin_cost_option_select",
                    format_func=lambda key: str(
                        option_master.loc[
                            option_master["option_norm_key"] == key, "option_name_display"
                        ].iloc[0]
                    ),
                )
                selected_row = option_master[option_master["option_norm_key"] == selected_option_label].iloc[0]
                st.caption(
                    f"선택 옵션상품명: `{_option_name_display(selected_row['option_name_norm'])}`"
                )
                input_col1, input_col2, input_col3 = st.columns(3)
                with input_col1:
                    input_unit_cost = st.number_input("단위원가", min_value=0, value=0, step=100)
                with input_col2:
                    input_pack_cost = st.number_input("포장/부자재 원가", min_value=0, value=0, step=100)
                with input_col3:
                    input_fulfillment_cost = st.number_input("풀필먼트 원가", min_value=0, value=0, step=100)
                effective_col, note_col = st.columns([2, 3])
                with effective_col:
                    input_effective_from = st.date_input(
                        "원가 적용일",
                        value=margin_base_date,
                        key="margin_cost_effective_from",
                    )
                with note_col:
                    input_note = st.text_input("메모(선택)", value="", key="margin_cost_note")
                save_submitted = st.form_submit_button("원가 저장", type="primary")
            if save_submitted:
                try:
                    save_result = save_option_cost_history(
                        product_name="__OPTION_NORM__",
                        option_name=str(selected_row["option_name_norm"] or ""),
                        effective_from=input_effective_from,
                        unit_cost=int(input_unit_cost),
                        pack_cost=int(input_pack_cost),
                        fulfillment_cost=int(input_fulfillment_cost),
                        note=input_note,
                    )
                    load_option_cost_history.clear()
                    if save_result == "created":
                        st.success("원가 이력이 신규 저장되었습니다.")
                    else:
                        st.success("동일 옵션/적용일 데이터가 있어 원가 이력을 갱신했습니다.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"원가 저장 중 오류가 발생했습니다: {exc}")

        if not cost_history_df.empty:
            section_heading("옵션 원가 이력(최근)", level=3)
            history_show = cost_history_df.copy()
            history_show["option_name"] = history_show["option_name"].map(_option_name_display)
            history_cols = [
                "option_name",
                "unit_cost",
                "pack_cost",
                "fulfillment_cost",
                "effective_from",
                "effective_to",
                "updated_at",
                "note",
            ]
            show_data_grid(history_show[history_cols].head(40), keep_input_order=True)

        section_heading("마진 결과표", level=3)
        margin_view_df = _build_margin_result_view(
            order_df,
            cost_history_df,
            start_date=margin_start_date,
            end_date=margin_base_date,
        )
        if margin_view_df.empty:
            st.caption("조회 구간에 마진 계산 대상 데이터가 없습니다.")
        else:
            total_revenue = float(pd.to_numeric(margin_view_df["net_revenue"], errors="coerce").fillna(0).sum())
            total_cost = float(pd.to_numeric(margin_view_df["estimated_cost"], errors="coerce").fillna(0).sum())
            total_margin = float(pd.to_numeric(margin_view_df["margin_amount"], errors="coerce").fillna(0).sum())
            margin_rate = (total_margin / total_revenue * 100.0) if total_revenue > 0 else 0.0
            no_cost_count = int((margin_view_df["cost_apply_status"] != "원가적용완료").sum())
            m1, m2, m3, m4, m5 = st.columns(5)
            m1.metric("총 순매출", f"{total_revenue:,.0f}원")
            m2.metric("총 적용원가", f"{total_cost:,.0f}원")
            m3.metric("총 마진액", f"{total_margin:,.0f}원")
            m4.metric("평균 마진율", f"{margin_rate:.1f}%")
            m5.metric("원가 미적용 옵션", f"{no_cost_count:,}개")
            if no_cost_count > 0:
                st.warning("일부 옵션은 유효 원가가 없어 마진이 과대 계산될 수 있습니다. 미입력 옵션 큐를 확인하세요.")
            margin_show = margin_view_df.copy()
            margin_show["option_name"] = margin_show["option_name"].map(_option_name_display)
            margin_cols = [
                "option_name",
                "order_count",
                "order_quantity",
                "net_revenue",
                "estimated_cost",
                "margin_amount",
                "margin_rate_pct",
                "applied_unit_cost",
                "latest_effective_from",
                "cost_apply_status",
            ]
            show_data_grid(margin_show[margin_cols], keep_input_order=True)

    with tab_kpi:
        section_heading("KPI")
        kpi_col1, kpi_col2 = st.columns(2)
        with kpi_col1:
            kpi_start_date = st.date_input(
                "시작",
                value=default_start,
                key="kpi_start_date",
            )
        with kpi_col2:
            kpi_end_date = st.date_input(
                "종료",
                value=default_end,
                key="kpi_end_date",
            )

        if kpi_start_date > kpi_end_date:
            st.stop()

        kpi_mask = (
            (order_df["date"].dt.date >= kpi_start_date)
            & (order_df["date"].dt.date <= kpi_end_date)
        )
        kpi_filtered_df = order_df[kpi_mask].copy()
        if kpi_filtered_df.empty:
            st.stop()

        period_days = (kpi_end_date - kpi_start_date).days + 1
        prev_start = kpi_start_date - timedelta(days=7)
        prev_end = kpi_end_date - timedelta(days=7)
        prev_mask = (
            (order_df["date"].dt.date >= prev_start)
            & (order_df["date"].dt.date <= prev_end)
        )
        prev_df = order_df[prev_mask].copy()

        compare_prev = not prev_df.empty
        whole = kpi_aggregate(kpi_filtered_df)
        prev_m = kpi_aggregate(prev_df)
        expected_sales = expected_sales_from_recent_7d(kpi_filtered_df)
        prev_expected_sales = expected_sales_from_recent_7d(prev_df)
        expected_settlement_total = float(
            pd.to_numeric(kpi_filtered_df["expected_settlement_amount"], errors="coerce").fillna(0).sum()
        )
        prev_expected_settlement_total = float(
            pd.to_numeric(prev_df["expected_settlement_amount"], errors="coerce").fillna(0).sum()
        )
        settlement_ratio_pct = (
            (expected_settlement_total / whole["total_amount"]) * 100.0
            if float(whole["total_amount"]) != 0.0
            else 0.0
        )
        settlement_diff_amount = expected_settlement_total - float(whole["total_amount"])

        def _prev_delta(curr: float, base: float) -> str | None:
            if not compare_prev:
                return None
            return f"{delta_rate(curr, base):.1f}%"

        compare_info = f"vs 1주 전 ({prev_start}~{prev_end})"

        with st.container(border=True):
            r1a, r1b, r1c, r1d = st.columns(4)
            r1a.metric(
                f"순매출 ({compare_info})",
                f"{whole['total_amount']:,.0f}원",
                _prev_delta(whole["total_amount"], prev_m["total_amount"]),
            )
            r1b.metric(
                f"상품주문 ({compare_info})",
                f"{int(whole['order_count']):,}",
                _prev_delta(whole["order_count"], prev_m["order_count"]),
            )
            r1c.metric(
                f"판매 수량 합계 ({compare_info})",
                f"{whole['total_quantity']:,.0f}",
                _prev_delta(whole["total_quantity"], prev_m["total_quantity"]),
            )
            r1d.metric(
                f"고객 수 ({compare_info})",
                f"{int(whole['customer_count']):,}",
                _prev_delta(whole["customer_count"], prev_m["customer_count"]),
            )
            r2a, r2b, r2c, r2d = st.columns(4)
            r2a.metric(
                f"객단가 ({compare_info})",
                f"{whole['avg_order_value']:,.0f}원",
                _prev_delta(whole["avg_order_value"], prev_m["avg_order_value"]),
            )
            r2b.metric(
                f"최근7일 평균 일매출 ({compare_info})",
                f"{expected_sales:,.0f}원",
                _prev_delta(expected_sales, prev_expected_sales),
            )
            r2c.metric(
                f"정산예정금액 합계 ({compare_info})",
                f"{expected_settlement_total:,.0f}원",
                _prev_delta(expected_settlement_total, prev_expected_settlement_total),
            )
            r2c.caption(
                f"순매출 대비 {settlement_ratio_pct:.1f}% · 차액 {settlement_diff_amount:,.0f}원"
            )
            r2d.metric(
                "일수",
                f"{period_days}일",
                None,
            )

        st.markdown("")
        section_heading("일자별", level=3)
        kpi_daily_start = default_end - timedelta(days=6)
        kpi_daily_mask = (
            (order_df["date"].dt.date >= kpi_daily_start)
            & (order_df["date"].dt.date <= default_end)
        )
        kpi_daily_table = order_df[kpi_daily_mask].copy()
        kpi_daily_table["date"] = kpi_daily_table["date"].dt.date
        daily_kpi = _aggregate_kpi_daily(kpi_daily_table)
        cal = pd.DataFrame(
            {"date": pd.date_range(kpi_daily_start, default_end, freq="D").date}
        )
        daily_kpi = cal.merge(daily_kpi, on="date", how="left")
        for col in ("total_amount", "total_quantity"):
            daily_kpi[col] = pd.to_numeric(daily_kpi[col], errors="coerce").fillna(0.0)
        daily_kpi["order_count"] = daily_kpi["order_count"].fillna(0).astype(int)
        daily_kpi = add_avg_ticket_to_daily(daily_kpi)
        daily_kpi["date_label"] = daily_kpi["date"].map(_format_sales_date_label)
        daily_kpi = daily_kpi.sort_values("date", ascending=False)

        daily_kpi = daily_kpi[
            [
                "date_label",
                "total_amount",
                "avg_ticket",
                "order_count",
                "total_quantity",
            ]
        ]
        daily_kpi = append_daily_total_row(daily_kpi)
        show_data_grid(daily_kpi)

    with tab_detail:
        section_heading("분석상세")
        ana_col1, ana_col2 = st.columns(2)
        with ana_col1:
            analysis_start_date = st.date_input(
                "시작",
                value=default_start,
                key="analysis_start_date",
            )
        with ana_col2:
            analysis_end_date = st.date_input(
                "종료",
                value=default_end,
                key="analysis_end_date",
            )
        buyer_name_search = st.text_input("구매자", "", key="main_buyer_search")

        if analysis_start_date > analysis_end_date:
            st.stop()

        analysis_mask = (
            (order_df["date"].dt.date >= analysis_start_date)
            & (order_df["date"].dt.date <= analysis_end_date)
        )
        analysis_filtered_df = order_df[analysis_mask].copy()
        if buyer_name_search:
            analysis_filtered_df = analysis_filtered_df[
                analysis_filtered_df["buyer_name"].astype(str).str.contains(
                    buyer_name_search, case=False, na=False
                )
            ]
        if analysis_filtered_df.empty:
            st.stop()

        _rev_col = (
            "net_revenue" if "net_revenue" in analysis_filtered_df.columns else "amount"
        )

        tab_product_sales, tab_option_sales = st.tabs(["상품", "옵션"])

        with tab_product_sales:
            product_summary = _prepare_analysis_summary(
                analysis_filtered_df,
                group_key="product_name",
                revenue_column=_rev_col,
            )
            product_summary = _append_analysis_total_row(product_summary, name_col="product_name")
            product_summary = product_summary[
                [
                    "product_name",
                    "order_count",
                    "order_quantity",
                    "total_amount",
                    "sales_share_pct",
                    "sold_quantity",
                    "amount_per_order",
                ]
            ]
            show_data_grid(product_summary, keep_input_order=True)

        with tab_option_sales:
            option_name_summary = _prepare_analysis_summary(
                analysis_filtered_df,
                group_key="option_name",
                revenue_column=_rev_col,
            )
            option_name_summary = _append_analysis_total_row(option_name_summary, name_col="option_name")
            option_name_summary = option_name_summary[
                [
                    "option_name",
                    "order_count",
                    "order_quantity",
                    "total_amount",
                    "sales_share_pct",
                    "sold_quantity",
                    "amount_per_order",
                ]
            ]
            show_data_grid(option_name_summary, keep_input_order=True)

        with st.expander("상세 주문 원장 보기", expanded=False):
            detail_ledger, guidance_text = _prepare_detail_ledger_for_display(analysis_filtered_df)
            if guidance_text:
                st.caption(f"참조 · 매출집계일 안내: {guidance_text}")
            show_data_grid(detail_ledger)


if _require_login():
    main_content()
