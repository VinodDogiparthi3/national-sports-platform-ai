import sqlglot
from sqlglot import exp

from config import (
    BIGQUERY_DATASET_ID,
    BIGQUERY_PROJECT_ID,
    BIGQUERY_TABLE_ID,
)
from schema_metadata import get_bigquery_schema


ALLOWED_PROJECT = BIGQUERY_PROJECT_ID
ALLOWED_DATASET = BIGQUERY_DATASET_ID
ALLOWED_TABLE = BIGQUERY_TABLE_ID


class OutOfScopeQueryError(ValueError):
    pass


def get_allowed_columns() -> set[str]:
    """
    Get the approved columns dynamically from BigQuery metadata.
    """
    from google.cloud import bigquery

    full_table_id = (
        f"{BIGQUERY_PROJECT_ID}.{BIGQUERY_DATASET_ID}.{BIGQUERY_TABLE_ID}"
    )

    client = bigquery.Client(project=BIGQUERY_PROJECT_ID)
    table = client.get_table(full_table_id)

    return {
        field.name.lower()
        for field in table.schema
    }


def validate_sql(sql: str, row_limit: int | None = 100) -> str:
    """Validate a generated query; row_limit=None keeps its own LIMIT (used for exports)."""

    # -------------------------
    # Parse BigQuery SQL
    # -------------------------
    try:
        statements = sqlglot.parse(
            sql,
            read="bigquery"
        )
    except Exception as e:
        raise ValueError(
            f"Invalid BigQuery SQL generated: {e}"
        )

    # -------------------------
    # Only one statement
    # -------------------------
    if len(statements) != 1:
        raise ValueError(
            "Multiple SQL statements are not allowed."
        )

    expression = statements[0]

    # -------------------------
    # SELECT only
    # -------------------------
    if not isinstance(expression, exp.Select):
        raise ValueError(
            "Only SELECT queries are allowed."
        )

    # -------------------------
    # Validate table
    # -------------------------
    tables = list(
        expression.find_all(exp.Table)
    )

    if not tables:
        raise OutOfScopeQueryError(
            "No table found in SQL query."
        )

    for table in tables:

        table_name = table.name.lower()

        db_name = (
            table.db.lower()
            if table.db
            else ""
        )

        catalog_name = (
            table.catalog.lower()
            if table.catalog
            else ""
        )

        if table_name != ALLOWED_TABLE.lower():
            raise OutOfScopeQueryError(
                f"Table is not allowed: {table_name}"
            )

        # Require exact dataset
        if db_name != ALLOWED_DATASET.lower():
            raise OutOfScopeQueryError(
                f"Dataset is not allowed: {db_name}"
            )

        # Require exact project
        if catalog_name != ALLOWED_PROJECT.lower():
            raise OutOfScopeQueryError(
                f"Project is not allowed: {catalog_name}"
            )

    # -------------------------
    # Dynamic column allowlist
    # -------------------------
    allowed_columns = get_allowed_columns()

    # -------------------------
    # Collect SELECT aliases
    # -------------------------
    select_aliases = set()

    for select_expression in expression.expressions:

        alias_name = select_expression.alias

        if alias_name:
            select_aliases.add(
                alias_name.lower()
            )

    # -------------------------
    # Validate columns
    # -------------------------
    for column in expression.find_all(exp.Column):

        column_name = column.name.lower()

        if column_name == "*":
            continue

        # Example:
        # COUNT(*) AS registration_count
        # ORDER BY registration_count
        if column_name in select_aliases:
            continue

        if column_name not in allowed_columns:
            raise OutOfScopeQueryError(
                f"Column is not allowed: {column.name}"
            )

    # -------------------------
    # LIMIT <= row_limit
    # -------------------------
    limit = expression.args.get("limit")

    if row_limit is None:
        pass

    elif limit is None:

        expression = expression.limit(row_limit)

    else:

        try:
            limit_value = int(
                limit.expression.name
            )

            if limit_value > row_limit:
                expression.set(
                    "limit",
                    exp.Limit(
                        expression=exp.Literal.number(row_limit)
                    )
                )

        except Exception:
            raise ValueError(
                "Invalid LIMIT value."
            )

    # -------------------------
    # Return BigQuery SQL
    # -------------------------
    return expression.sql(
        dialect="bigquery"
    )