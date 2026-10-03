import psycopg

from config import (
    DB_HOST,
    DB_PORT,
    DB_NAME,
    DB_USER,
    get_db_password,
)


def execute_sql(sql: str):

    password = get_db_password()

    connection_string = (
        f"host={DB_HOST} "
        f"port={DB_PORT} "
        f"dbname={DB_NAME} "
        f"user={DB_USER} "
        f"password={password}"
    )

    with psycopg.connect(connection_string) as conn:

        # Database-level safety
        conn.execute("SET TRANSACTION READ ONLY")
        conn.execute("SET LOCAL statement_timeout = '10s'")

        with conn.cursor() as cur:

            cur.execute(sql)

            columns = [
                description.name
                for description in cur.description
            ]

            rows = cur.fetchall()

    return columns, rows