import os

from sqlalchemy import create_engine, text


DDL = """
CREATE TABLE IF NOT EXISTS daily_summary (
    id INT AUTO_INCREMENT PRIMARY KEY,
    date DATE NOT NULL,
    product_id VARCHAR(100),
    option_id VARCHAR(100),
    orders INT DEFAULT 0,
    revenue INT DEFAULT 0,
    cancel_count INT DEFAULT 0,
    refund_amount INT DEFAULT 0,
    profit INT DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uniq_daily (date, product_id, option_id),
    INDEX idx_date (date),
    INDEX idx_product (product_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""


def main() -> None:
    raw_url = (
        os.getenv("MARIADB_PUBLIC_URL")
        or os.getenv("MARIADB_PRIVATE_URL")
        or os.getenv("DATABASE_URL")
    )
    if not raw_url:
        raise RuntimeError(
            "Database URL not found. Set MARIADB_PUBLIC_URL, MARIADB_PRIVATE_URL, or DATABASE_URL."
        )
    url = raw_url.replace("mariadb://", "mysql+pymysql://", 1)
    engine = create_engine(url)

    with engine.begin() as conn:
        conn.execute(text(DDL))
        exists = conn.execute(
            text(
                """
                SELECT COUNT(*)
                FROM information_schema.tables
                WHERE table_schema = DATABASE()
                  AND table_name = 'daily_summary'
                """
            )
        ).scalar()

    print(f"daily_summary_exists={bool(exists)}")


if __name__ == "__main__":
    main()

