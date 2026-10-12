import sqlglot
from sqlglot import exp
import re

from config import (
    BIGQUERY_DATASET_ID,
    BIGQUERY_PROJECT_ID,
    BIGQUERY_TABLE_ID,
)
from schema_metadata import get_bigquery_schema
from nafes_schema import (
    ALLOWED_DERIVED_COLUMNS,
    APPROVED_COLUMNS_BY_VIEW,
    ALLOWED_RESULT_ALIASES,
    LICENSE_INTERNAL_COLUMNS,
    LICENSE_VIEW,
    LICENSE_VIEW_NAME,
    NAFES_MAX_ROWS,
    NAFES_PROJECT,
    NAFES_DATASET,
    REQUEST_INTERNAL_COLUMNS,
    REQUEST_VIEW,
    REQUEST_VIEW_NAME,
    SENSITIVE_COLUMNS,
)


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


def _validate_nafes_dedup_semantics(expression, physical_tables: list[str]) -> None:
    ctes_by_name = {
        cte.alias_or_name.lower(): cte
        for cte in expression.find_all(exp.CTE)
        if cte.alias_or_name
    }

    def require_dedup_ctes(
        id_cte_name: str,
        clean_cte_name: str,
        id_column: str,
        id_projection_sources: dict[str, str],
        clean_projection_sources: dict[str, str],
        variant_columns: set[str],
    ) -> None:
        id_cte = ctes_by_name.get(id_cte_name)
        clean_cte = ctes_by_name.get(clean_cte_name)
        if id_cte is None or clean_cte is None or not isinstance(id_cte.this, exp.Select):
            raise ValueError(
                f"Queries over Nafes data must deduplicate through {id_cte_name} "
                f"and exclude conflicts through {clean_cte_name}."
            )

        id_query = id_cte.this
        group = id_query.args.get("group")
        group_columns = {
            column.name.lower() for column in group.find_all(exp.Column)
        } if group else set()
        id_projections = {
            projection.alias_or_name.lower(): projection
            for projection in id_query.expressions
            if projection.alias_or_name
        }
        id_aliases = {
            alias for alias, source in id_projection_sources.items()
            if source == id_column and alias.startswith("_")
        }
        if (
            not ({id_column} | id_aliases).intersection(group_columns)
            or not set(id_projection_sources).issubset(id_projections)
        ):
            raise ValueError(f"{id_cte_name} must group by {id_column} and expose duplicate checks.")
        for alias, source_column in id_projection_sources.items():
            projection = id_projections[alias]
            referenced_columns = {
                column.name.lower() for column in projection.find_all(exp.Column)
            }
            if source_column not in referenced_columns:
                raise ValueError(f"{id_cte_name}.{alias} must be derived from {source_column}.")
            if alias in variant_columns and not any(
                isinstance(node, exp.Distinct) for node in projection.walk()
            ):
                raise ValueError(f"{id_cte_name}.{alias} must use COUNT(DISTINCT ...).")

        clean_query = clean_cte.this
        if not isinstance(clean_query, exp.Select):
            raise ValueError(f"{clean_cte_name} must be a SELECT CTE.")
        clean_projections = {
            projection.alias_or_name.lower(): projection
            for projection in clean_query.expressions
            if projection.alias_or_name
        }
        clean_sources = {
            table.name.lower() for table in clean_query.find_all(exp.Table)
        }
        where = clean_query.args.get("where")
        if (
            id_cte_name not in clean_sources
            or not set(clean_projection_sources).issubset(clean_projections)
            or where is None
        ):
            raise ValueError(f"{clean_cte_name} must filter deduplicated records before aggregation.")
        for alias, source_column in clean_projection_sources.items():
            referenced_columns = {
                column.name.lower() for column in clean_projections[alias].find_all(exp.Column)
            }
            if source_column not in referenced_columns:
                raise ValueError(f"{clean_cte_name}.{alias} must preserve {source_column}.")

        def safe_and_filters(node):
            if isinstance(node, exp.Or):
                return False, set()
            if isinstance(node, exp.And):
                left_safe, left_columns = safe_and_filters(node.this)
                right_safe, right_columns = safe_and_filters(node.expression)
                return left_safe and right_safe, left_columns | right_columns
            if isinstance(node, exp.EQ):
                columns = list(node.find_all(exp.Column))
                literals = list(node.find_all(exp.Literal))
                if len(columns) == 1 and len(literals) == 1 and literals[0].name == "1":
                    return True, {columns[0].name.lower()}
            return True, set()

        filter_safe, filtered_columns = safe_and_filters(where.this)
        if not filter_safe or not variant_columns.issubset(filtered_columns):
            raise ValueError(
                f"{clean_cte_name} must exclude records with conflicting duplicate values."
            )

    license_detail_columns = {"facility_id", "ntw_expirydate", "license_status_name"}
    if LICENSE_VIEW_NAME in physical_tables and any(
        column.name.lower() in license_detail_columns
        for column in expression.find_all(exp.Column)
    ):
        license_variants = {
            "_license_id", "facility_id", "license_expiry_date", "license_source_status",
            "license_type_name", "facility_id_variants", "expiry_date_variants",
            "license_status_variants", "license_type_variants",
        }
        require_dedup_ctes(
            "license_by_id", "unambiguous_licenses", "license_id",
            {
                "_license_id": "license_id",
                "facility_id": "facility_id",
                "license_expiry_date": "ntw_expirydate",
                "license_source_status": "license_status_name",
                "license_type_name": "license_type_name",
                "facility_id_variants": "facility_id",
                "expiry_date_variants": "ntw_expirydate",
                "license_status_variants": "license_status_name",
                "license_type_variants": "license_type_name",
            },
            {
                "_license_id": "_license_id",
                "facility_id": "facility_id",
                "license_expiry_date": "license_expiry_date",
                "license_source_status": "license_source_status",
                "license_type_name": "license_type_name",
                "facility_id_variants": "facility_id_variants",
                "expiry_date_variants": "expiry_date_variants",
                "license_status_variants": "license_status_variants",
                "license_type_variants": "license_type_variants",
            },
            {"facility_id_variants", "expiry_date_variants", "license_status_variants", "license_type_variants"},
        )

    request_detail_columns = {
        "facilityid", "request_status_name", "request_date", "region_name",
        "city_name", "request_type", "request_type_ar", "facility_type_name",
    }
    if REQUEST_VIEW_NAME in physical_tables and any(
        column.name.lower() in request_detail_columns
        for column in expression.find_all(exp.Column)
    ):
        request_variants = {
            "_request_id", "facility_id", "request_date", "request_status",
            "facility_id_variants", "request_status_variants", "request_date_variants",
        }
        require_dedup_ctes(
            "request_by_id", "unambiguous_requests", "request_id",
            {
                "_request_id": "request_id",
                "facility_id": "facilityid",
                "request_date": "request_date",
                "request_status": "request_status_name",
                "facility_id_variants": "facilityid",
                "request_status_variants": "request_status_name",
                "request_date_variants": "request_date",
            },
            {
                "_request_id": "_request_id",
                "facility_id": "facility_id",
                "request_date": "request_date",
                "request_status": "request_status",
                "facility_id_variants": "facility_id_variants",
                "request_status_variants": "request_status_variants",
                "request_date_variants": "request_date_variants",
            },
            {"facility_id_variants", "request_status_variants", "request_date_variants"},
        )

    if LICENSE_VIEW_NAME in physical_tables and REQUEST_VIEW_NAME in physical_tables:
        ctes = {
            cte.alias_or_name.lower(): cte
            for cte in expression.find_all(exp.CTE)
            if cte.alias_or_name
        }

        def require_facility_aggregate(
            aggregate_name: str,
            source_name: str,
            count_alias: str,
        ) -> None:
            cte = ctes.get(aggregate_name)
            if cte is None or not isinstance(cte.this, exp.Select):
                raise ValueError(f"Two-view queries must aggregate through {aggregate_name}.")
            query = cte.this
            group = query.args.get("group")
            group_columns = {column.name.lower() for column in group.find_all(exp.Column)} if group else set()
            source_names = {table.name.lower() for table in query.find_all(exp.Table)}
            projections = {
                projection.alias_or_name.lower(): projection
                for projection in query.expressions
                if projection.alias_or_name
            }
            if source_name not in source_names or "facility_id" not in group_columns:
                raise ValueError(f"{aggregate_name} must group the deduplicated source by facility_id.")
            if count_alias not in projections or not any(
                isinstance(node, exp.Count) for node in projections[count_alias].walk()
            ):
                raise ValueError(f"{aggregate_name} must count unique source records per facility.")

            sql_text = query.sql(dialect="bigquery").casefold()
            if (
                not re.search(
                    r"(?:facility_id\s+is\s+not\s+null|not\s+facility_id\s+is\s+null)",
                    sql_text,
                )
                or not re.search(
                    r"(?:trim\(\s*facility_id\s*\)\s*(?:<>|!=)\s*''|"
                    r"not\s+trim\(\s*facility_id\s*\)\s*=\s*'')",
                    sql_text,
                )
            ):
                raise ValueError(f"{aggregate_name} must exclude missing facility IDs before joining.")

        require_facility_aggregate("licenses_by_facility", "unambiguous_licenses", "unique_license_count")
        require_facility_aggregate("requests_by_facility", "unambiguous_requests", "associated_request_count")

        root_sql = expression.sql(dialect="bigquery").casefold()
        if not re.search(
            r"from\s+licenses_by_facility\s+(?:as\s+)?[a-z_][a-z0-9_]*\s+"
            r"(?:left\s+(?:outer\s+)?join|inner\s+join)\s+"
            r"requests_by_facility\s+(?:as\s+)?[a-z_][a-z0-9_]*\s+on\s+"
            r"[a-z_][a-z0-9_]*\.facility_id\s*=\s*[a-z_][a-z0-9_]*\.facility_id",
            root_sql,
        ):
            raise ValueError("Two-view queries must join the separate facility aggregates by facility_id.")


def validate_nafes_sql(
    sql: str,
    row_limit: int = NAFES_MAX_ROWS,
    required_parameters: set[str] | None = None,
    enforce_deduplication: bool = True,
) -> str:
    """Validate SQL for the two approved Nafes views without changing V3 rules."""
    try:
        statements = sqlglot.parse(sql, read="bigquery")
    except Exception as exc:
        raise ValueError(f"Invalid BigQuery SQL generated: {exc}") from exc

    if len(statements) != 1 or statements[0] is None:
        raise ValueError("Multiple SQL statements are not allowed.")

    expression = statements[0]
    if not isinstance(expression, exp.Select):
        raise ValueError("Only SELECT queries are allowed.")

    forbidden_nodes = {
        "alter", "command", "copy", "create", "delete", "drop", "insert",
        "merge", "rename", "replace", "truncate", "update",
    }
    if any(node.__class__.__name__.lower() in forbidden_nodes for node in expression.walk()):
        raise ValueError("Only read-only SELECT queries are allowed.")

    cte_names = {
        cte.alias_or_name.lower()
        for cte in expression.find_all(exp.CTE)
        if cte.alias_or_name
    }
    physical_tables = []
    table_aliases: dict[str, str] = {}
    derived_aliases = set(cte_names)
    approved_full_names = {
        LICENSE_VIEW_NAME: LICENSE_VIEW,
        REQUEST_VIEW_NAME: REQUEST_VIEW,
    }

    for table in expression.find_all(exp.Table):
        table_name = table.name.lower()
        if table_name in cte_names and not (table.catalog or table.db):
            if table.alias_or_name:
                derived_aliases.add(table.alias_or_name.lower())
            continue

        catalog_name = table.catalog or ""
        dataset_name = table.db or ""
        full_name = f"{catalog_name}.{dataset_name}.{table.name}".lower()
        expected = approved_full_names.get(table_name)
        if (
            expected is None
            or full_name != expected.lower()
            or catalog_name.lower() != NAFES_PROJECT.lower()
            or dataset_name.lower() != NAFES_DATASET.lower()
        ):
            raise OutOfScopeQueryError(f"Table is not allowed: {full_name}")

        physical_tables.append(table_name)
        table_aliases[table.alias_or_name.lower()] = table_name
        table_aliases[table_name] = table_name

    if not physical_tables:
        raise OutOfScopeQueryError("No approved Nafes view found in SQL query.")

    derived_aliases.update(
        subquery.alias_or_name.lower()
        for subquery in expression.find_all(exp.Subquery)
        if subquery.alias_or_name
    )
    internal_id_ctes = {
        id(column): cte.alias_or_name.lower()
        for cte in expression.find_all(exp.CTE)
        if cte.alias_or_name
        for column in cte.this.find_all(exp.Column)
        if column.name.lower() in LICENSE_INTERNAL_COLUMNS | REQUEST_INTERNAL_COLUMNS
    }
    all_safe_columns = set().union(*APPROVED_COLUMNS_BY_VIEW.values())
    internal_columns = LICENSE_INTERNAL_COLUMNS | REQUEST_INTERNAL_COLUMNS
    selected_aliases = {
        projection.alias_or_name.lower()
        for projection in expression.expressions
        if projection.alias_or_name
    }

    for column in expression.find_all(exp.Column):
        column_name = column.name.lower()
        if column_name in SENSITIVE_COLUMNS:
            raise OutOfScopeQueryError(f"Sensitive column is not allowed: {column.name}")

        qualifier = column.table.lower()
        if qualifier in table_aliases:
            view_name = table_aliases[qualifier]
            if column_name not in APPROVED_COLUMNS_BY_VIEW[view_name]:
                raise OutOfScopeQueryError(f"Column is not allowed: {column.name}")
        elif qualifier in derived_aliases:
            if (
                column_name not in all_safe_columns
                and column_name not in ALLOWED_DERIVED_COLUMNS
                and column_name not in ALLOWED_RESULT_ALIASES
            ):
                raise OutOfScopeQueryError(f"Derived column is not allowed: {column.name}")
        elif qualifier:
            raise OutOfScopeQueryError(f"Table alias is not allowed: {column.table}")
        else:
            views_with_column = [
                view for view in physical_tables
                if column_name in APPROVED_COLUMNS_BY_VIEW[view]
            ]
            in_derived_scope = (
                column.find_ancestor(exp.CTE) is not None
                or column.find_ancestor(exp.Subquery) is not None
            )
            if in_derived_scope and (
                column_name in all_safe_columns
                or column_name in ALLOWED_RESULT_ALIASES
                or column_name in ALLOWED_DERIVED_COLUMNS
            ):
                continue
            if column_name in ALLOWED_DERIVED_COLUMNS:
                continue
            if not views_with_column or (
                len(set(physical_tables)) > 1
                and len(views_with_column) != len(set(physical_tables))
            ):
                raise OutOfScopeQueryError(
                    f"Column must be qualified and approved: {column.name}"
                )

        if column_name in internal_columns:
            in_distinct_count = (
                isinstance(column.parent, exp.Distinct)
                and isinstance(column.parent.parent, exp.Count)
            )
            expected_cte = (
                "license_by_id" if column_name in LICENSE_INTERNAL_COLUMNS
                else "request_by_id"
            )
            is_selected_order_alias = (
                column_name == "license_id"
                and column_name in selected_aliases
                and column.find_ancestor(exp.Order) is not None
            )
            if (
                not in_distinct_count
                and internal_id_ctes.get(id(column)) != expected_cte
                and not is_selected_order_alias
            ):
                raise OutOfScopeQueryError(
                    f"{column.name} may only be used in COUNT(DISTINCT ...) or its approved deduplication CTE."
                )

    for star in expression.find_all(exp.Star):
        if not isinstance(star.parent, exp.Count):
            raise OutOfScopeQueryError("SELECT * is not allowed for Nafes data.")

    for projection in expression.expressions:
        output_name = projection.alias_or_name.lower()
        if output_name in SENSITIVE_COLUMNS or (
            output_name in internal_columns and output_name != "license_id"
        ):
            raise OutOfScopeQueryError(f"Sensitive result column is not allowed: {output_name}")
        if output_name not in ALLOWED_RESULT_ALIASES and output_name not in all_safe_columns:
            raise OutOfScopeQueryError(f"Result column is not approved: {output_name}")
        if (
            output_name == "calculated_expiry_status"
            and "'expiring soon'" not in projection.sql(dialect="bigquery").casefold()
        ):
            raise ValueError(
                "A next-window license listing must label its calculated expiry status as Expiring Soon."
            )

    parameters = {
        name.lower()
        for name in re.findall(r"@([A-Za-z_][A-Za-z0-9_]*)", sql)
    }
    allowed_parameters = {"facility_id", "window_days", "freshness_threshold_days"}
    if not parameters.issubset(allowed_parameters):
        raise ValueError("SQL contains an unsupported query parameter.")
    if not (required_parameters or set()).issubset(parameters):
        raise ValueError("SQL is missing a required parameter.")

    uses_expiry_window = (
        any(column.name.lower() == "ntw_expirydate" for column in expression.find_all(exp.Column))
        and re.search(r"CURRENT_DATE\s*\(", sql, re.IGNORECASE) is not None
    )
    if uses_expiry_window:
        if "window_days" not in parameters:
            raise ValueError("Expiry queries must use the configured window parameter.")
        if not re.search(r"CURRENT_DATE\s*\(\s*['\"]UTC['\"]\s*\)", sql, re.IGNORECASE):
            raise ValueError("Expiry queries must calculate dates in UTC.")

    allowed_string_literals = {
        "", "utc", "expiry_date_past", "expires_within_window",
        "expiry_date_future", "missing_expiry_date", "expired",
        "expiring soon", "unexpired", "not expired", "valid",
    }
    for literal in expression.find_all(exp.Literal):
        if literal.is_string and literal.this.casefold() not in allowed_string_literals:
            raise ValueError("SQL contains an unapproved string literal.")

    if enforce_deduplication:
        _validate_nafes_dedup_semantics(expression, list(set(physical_tables)))

    limit = expression.args.get("limit")
    if limit is None:
        expression = expression.limit(row_limit)
    else:
        try:
            limit_value = int(limit.expression.name)
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError("Invalid LIMIT value.") from exc
        if limit_value > row_limit:
            expression.set(
                "limit",
                exp.Limit(expression=exp.Literal.number(row_limit)),
            )

    return expression.sql(dialect="bigquery")