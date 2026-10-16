import sqlglot
from sqlglot import exp

from config import (
    BIGQUERY_DATASET_ID,
    BIGQUERY_PROJECT_ID,
    BIGQUERY_TABLE_ID,
)
from schema_metadata import get_view_columns
from view_catalog import find_source, is_join_verified


ALLOWED_PROJECT = BIGQUERY_PROJECT_ID
ALLOWED_DATASET = BIGQUERY_DATASET_ID
ALLOWED_TABLE = BIGQUERY_TABLE_ID


class OutOfScopeQueryError(ValueError):
    pass


def get_allowed_columns(view_ids: set[str] | None = None) -> set[str]:
    """Approved columns from cached BigQuery metadata for the given views (default: NSP)."""
    return {
        name.lower()
        for view_id in (view_ids or {BIGQUERY_TABLE_ID})
        for name, _, _ in get_view_columns(view_id)
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

    used_views = set()
    resolved_sources = {}

    for table in tables:

        approved = find_source(
            table.catalog or "",
            table.db or "",
            table.name or "",
        )

        if approved is None:
            raise OutOfScopeQueryError(
                f"Table is not allowed: {table.catalog}.{table.db}.{table.name}"
            )

        used_views.add(approved.source_id)
        resolved_sources[approved.source_id] = approved

    # Never assume relationships between views.
    if not is_join_verified(used_views):
        raise OutOfScopeQueryError(
            "Joining these views is not allowed: no verified relationship."
        )

    # -------------------------
    # Dynamic column allowlist
    # -------------------------
    allowed_columns = get_allowed_columns(used_views)
    dynamic_sources = {
        source_id
        for source_id in used_views
        if getattr(resolved_sources[source_id], "query_authorized", False)
    }

    if dynamic_sources:
        for select_expression in expression.expressions:
            if isinstance(select_expression, exp.Star) or (
                isinstance(select_expression, exp.Column) and select_expression.name == "*"
            ):
                raise OutOfScopeQueryError("SELECT * is not allowed for dynamic sources; use explicitly authorized columns.")

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