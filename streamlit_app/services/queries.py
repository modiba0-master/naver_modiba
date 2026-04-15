from datetime import date

import pandas as pd
from sqlalchemy import Engine, text

def _date_filter_sql(date_col: str = "business_date") -> str:
    return f"""
    WHERE (:start_date IS NULL OR DATE({date_col}) >= :start_date)
      AND (:end_date IS NULL OR DATE({date_col}) <= :end_date)
    """


def get_main_kpis(engine: Engine, target_date: date) -> pd.DataFrame:
    query = text(
        """
        SELECT
            COALESCE(SUM(CASE WHEN business_date = :target_date THEN amount ELSE 0 END), 0) AS today_revenue,
            COALESCE(SUM(CASE WHEN business_date = :target_date THEN 1 ELSE 0 END), 0) AS total_orders,
            COALESCE(SUM(CASE WHEN business_date = :target_date THEN amount ELSE 0 END), 0) AS total_profit
        FROM orders
        WHERE order_status IN ('신규주문', '배송준비')
        """
    )
    return pd.read_sql(query, engine, params={"target_date": target_date})


def get_top_products(engine: Engine, target_date: date, limit: int = 5) -> pd.DataFrame:
    query = text(
        """
        SELECT
            product_name,
            COALESCE(SUM(amount), 0) AS revenue
        FROM orders
        WHERE business_date = :target_date
          AND order_status IN ('신규주문', '배송준비')
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
          AND order_status IN ('신규주문', '배송준비')
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
    hour_query = text(
        f"""
        SELECT
            HOUR(payment_date) AS hour_of_day,
            COUNT(*) AS orders
        FROM orders
        {_date_filter_sql("business_date")}
          AND order_status IN ('신규주문', '배송준비')
        GROUP BY HOUR(payment_date)
        ORDER BY hour_of_day
        """
    )
    weekday_query = text(
        f"""
        SELECT
            WEEKDAY(business_date) AS weekday_num,
            COUNT(*) AS orders
        FROM orders
        {_date_filter_sql("business_date")}
          AND order_status IN ('신규주문', '배송준비')
        GROUP BY WEEKDAY(business_date)
        ORDER BY weekday_num
        """
    )
    params = {"start_date": start_date, "end_date": end_date}
    hour_df = pd.read_sql(hour_query, engine, params=params)
    weekday_df = pd.read_sql(weekday_query, engine, params=params)
    return hour_df, weekday_df

