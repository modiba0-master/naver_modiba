from datetime import date

import pandas as pd
from sqlalchemy import Engine, text


def _hour_sql(dialect: str) -> str:
    """payment_date에서 시(hour)만 추출(DB 값 그대로)."""
    if dialect == "sqlite":
        return "CAST(strftime('%H', payment_date) AS INTEGER)"
    if dialect == "postgresql":
        return "CAST(EXTRACT(HOUR FROM payment_date) AS INTEGER)"
    return "HOUR(payment_date)"


def _weekday_sql(dialect: str) -> str:
    """MySQL WEEKDAY와 맞춤: 월=0 … 일=6."""
    if dialect == "sqlite":
        return "(CAST(strftime('%w', payment_date) AS INTEGER) + 6) % 7"
    if dialect == "postgresql":
        return "(CAST(EXTRACT(ISODOW FROM CAST(payment_date AS DATE)) AS INTEGER) - 1 + 7) % 7"
    return "WEEKDAY(DATE(payment_date))"


def _date_filter_sql(date_col: str = "business_date") -> str:
    return f"""
    WHERE (:start_date IS NULL OR {date_col} >= :start_date)
      AND (:end_date IS NULL OR {date_col} <= :end_date)
    """


def get_main_kpis(engine: Engine, target_date: date) -> pd.DataFrame:
    query = text(
        """
        SELECT
            COALESCE(SUM(CASE WHEN business_date = :target_date THEN amount ELSE 0 END), 0) AS today_revenue,
            COALESCE(SUM(CASE WHEN business_date = :target_date THEN 1 ELSE 0 END), 0) AS total_orders,
            COALESCE(SUM(CASE WHEN business_date = :target_date THEN amount ELSE 0 END), 0) AS total_profit
        FROM orders
        WHERE payment_date IS NOT NULL
        """
    )
    return pd.read_sql(query, engine, params={"target_date": target_date})


def get_top_products(engine: Engine, target_date: date, limit: int = 5) -> pd.DataFrame:
    query = text(
        f"""
        SELECT
            product_name,
            COALESCE(SUM(amount), 0) AS revenue
        FROM orders
        WHERE business_date = :target_date
        GROUP BY product_name
        ORDER BY revenue DESC
        LIMIT :limit_count
        """
    )
    return pd.read_sql(query, engine, params={"target_date": target_date, "limit_count": limit})


def get_product_analysis(engine: Engine, start_date: date | None, end_date: date | None) -> pd.DataFrame:
    query = text(
        f"""
        SELECT
            business_date AS order_date,
            product_name,
            COUNT(*) AS orders,
            COALESCE(SUM(amount), 0) AS revenue,
            COALESCE(SUM(amount), 0) AS profit
        FROM orders
        {_date_filter_sql("business_date")}
        GROUP BY business_date, product_name
        ORDER BY order_date ASC, revenue DESC
        """
    )
    return pd.read_sql(query, engine, params={"start_date": start_date, "end_date": end_date})


def get_option_analysis(engine: Engine, start_date: date | None, end_date: date | None) -> pd.DataFrame:
    query = text(
        f"""
        SELECT
            option_name,
            COUNT(*) AS orders,
            COALESCE(
                SUM(
                    CASE WHEN order_status IN ('취소', '주문취소', 'CANCELLED') THEN 1 ELSE 0 END
                ),
                0
            ) AS cancel_count
        FROM orders
        {_date_filter_sql("business_date")}
        GROUP BY option_name
        ORDER BY orders DESC
        """
    )
    df = pd.read_sql(query, engine, params={"start_date": start_date, "end_date": end_date})
    if df.empty:
        df["cancel_rate"] = []
        return df
    df["cancel_rate"] = (df["cancel_count"] / df["orders"]).fillna(0.0)
    return df


def get_time_analysis(engine: Engine, start_date: date | None, end_date: date | None) -> tuple[pd.DataFrame, pd.DataFrame]:
    dialect = engine.dialect.name
    h = _hour_sql(dialect)
    w = _weekday_sql(dialect)
    hour_query = text(
        f"""
        SELECT
            {h} AS hour_of_day,
            COUNT(*) AS orders
        FROM orders
        {_date_filter_sql("business_date")}
        GROUP BY {h}
        ORDER BY hour_of_day
        """
    )
    weekday_query = text(
        f"""
        SELECT
            {w} AS weekday_num,
            COUNT(*) AS orders
        FROM orders
        {_date_filter_sql("business_date")}
        GROUP BY {w}
        ORDER BY weekday_num
        """
    )
    params = {"start_date": start_date, "end_date": end_date}
    hour_df = pd.read_sql(hour_query, engine, params=params)
    weekday_df = pd.read_sql(weekday_query, engine, params=params)
    return hour_df, weekday_df

