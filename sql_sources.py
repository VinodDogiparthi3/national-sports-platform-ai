"""SQLGlot helpers for resolving physical table references through query scopes."""

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import Scope, traverse_scope


def resolved_table_references(sql: str) -> tuple[exp.Table, ...]:
    """Return unique physical table references, excluding CTE and derived-table aliases."""
    try:
        statements = sqlglot.parse(sql, read="bigquery")
    except Exception as error:
        raise ValueError("Invalid BigQuery SQL.") from error
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        raise ValueError("Only one SELECT statement is allowed.")

    references = {}
    for scope in traverse_scope(statements[0]):
        for table in scope.tables:
            source = scope.sources.get(table.alias_or_name)
            if isinstance(source, Scope):
                continue
            resolved = source if isinstance(source, exp.Table) else table
            identity = tuple(
                (part or "").casefold()
                for part in (resolved.catalog, resolved.db, resolved.name)
            )
            references.setdefault(identity, resolved)
    return tuple(references.values())