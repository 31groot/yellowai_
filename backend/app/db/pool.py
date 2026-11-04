from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app import config

pool: ConnectionPool | None = None


def open_pool(dsn: str | None = None) -> ConnectionPool:
    global pool
    pool = ConnectionPool(
        dsn or config.DATABASE_URL,
        min_size=2,
        max_size=20,
        kwargs={"row_factory": dict_row, "autocommit": True},
        open=True,
    )
    pool.wait()
    return pool


def close_pool() -> None:
    global pool
    if pool is not None:
        pool.close()
        pool = None


def get_pool() -> ConnectionPool:
    if pool is None:
        open_pool()
    return pool
