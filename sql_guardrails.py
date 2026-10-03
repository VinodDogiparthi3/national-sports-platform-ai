import sqlglot
from sqlglot import exp

from schema_metadata import get_bigquery_schema


ALLOWED_PROJECT = "mos-gcp-host-dev-prj"
ALLOWED_DATASET = "dw_nsp_api_views"
ALLOWED_TABLE = "nsp_base_view_01"


def get_allowed_columns() -> set[str]:
    """
    Get the approved columns dynamically from BigQuery metadata.
    """
    from google.cloud import bigquery

    full_table_id = (
        f"{ALLOWED_PROJECT}.{ALLOWED_DATASET}.{ALLOWED_TABLE}"
    )

    client = bigquery.Client(project=ALLOWED_PROJECT)
    table = client.get_table(full_table_id)

    return {
        field.name.lower()
        for field in table.schema
    }


def validate_sql(sql: str) -> str:

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
        raise ValueError(
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
            raise ValueError(
                f"Table is not allowed: {table_name}"
            )

        # Require exact dataset
        if db_name != ALLOWED_DATASET.lower():
            raise ValueError(
                f"Dataset is not allowed: {db_name}"
            )

        # Require exact project
        if catalog_name != ALLOWED_PROJECT.lower():
            raise ValueError(
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
            raise ValueError(
                f"Column is not allowed: {column.name}"
            )

    # -------------------------
    # LIMIT <= 100
    # -------------------------
    limit = expression.args.get("limit")

    if limit is None:

        expression = expression.limit(100)

    else:

        try:
            limit_value = int(
                limit.expression.name
            )

            if limit_value > 100:
                expression.set(
                    "limit",
                    exp.Limit(
                        expression=exp.Literal.number(100)
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