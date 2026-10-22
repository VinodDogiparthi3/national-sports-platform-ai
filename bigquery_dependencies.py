"""Fail-closed check of the tables BigQuery itself reports for a PERSONAL_GCP dry run."""

from config import (
    PERSONAL_GCP_ALLOWED_TABLES,
    PERSONAL_GCP_ALLOWED_VIEWS,
    PERSONAL_GCP_PROJECT_ID,
    PERSONAL_GCP_VIEW_DEPENDENCY_CLOSURE,
)
from sql_sources import resolved_table_references

# BigQuery omits referenced tables beyond this count, so a list this long may be incomplete.
MAX_COMPLETE_REFERENCED_TABLES = 50


class DependencyVerificationError(ValueError):
    """Raised when actual BigQuery dependencies cannot be proven to be approved."""


def _approved_dependencies(sql: str) -> frozenset[str]:
    personal_project = (PERSONAL_GCP_PROJECT_ID or "").casefold()
    if not personal_project:
        raise DependencyVerificationError("PERSONAL_GCP project is not configured.")
    try:
        references = resolved_table_references(sql)
    except ValueError as error:
        raise DependencyVerificationError("SQL sources could not be resolved.") from error
    approved = set()
    for table in references:
        project_id = (table.catalog or "").casefold()
        dataset_id = (table.db or "").casefold()
        object_id = (table.name or "").casefold()
        source_id = f"{dataset_id}.{object_id}"
        if project_id != personal_project:
            raise DependencyVerificationError("SQL references a source outside the personal project.")
        if object_id in PERSONAL_GCP_ALLOWED_TABLES.get(dataset_id, frozenset()):
            approved.add(source_id)
        elif source_id in PERSONAL_GCP_VIEW_DEPENDENCY_CLOSURE and object_id in PERSONAL_GCP_ALLOWED_VIEWS.get(dataset_id, frozenset()):
            approved.add(source_id)
            approved |= PERSONAL_GCP_VIEW_DEPENDENCY_CLOSURE[source_id]
        else:
            raise DependencyVerificationError("SQL references a source outside the approved allowlists.")
    if not approved:
        raise DependencyVerificationError("SQL references no approved source.")
    return frozenset(approved)


def verify_personal_dry_run(sql: str, query_job) -> None:
    """Reject unless every table BigQuery reports is within the SQL's declared approved dependencies."""
    if getattr(query_job, "statement_type", None) != "SELECT":
        raise DependencyVerificationError("BigQuery did not confirm a read-only SELECT statement.")
    try:
        referenced = list(query_job.referenced_tables or ())
    except Exception as error:
        raise DependencyVerificationError("BigQuery dependency metadata is unavailable.") from error
    if not referenced:
        raise DependencyVerificationError("BigQuery dependency metadata is unavailable.")
    if len(referenced) >= MAX_COMPLETE_REFERENCED_TABLES:
        raise DependencyVerificationError("BigQuery dependency metadata may be incomplete.")
    if getattr(query_job, "referenced_property_graphs", None):
        raise DependencyVerificationError("Property graph references are not allowed.")

    approved = _approved_dependencies(sql)
    personal_project = PERSONAL_GCP_PROJECT_ID.casefold()
    for reference in referenced:
        project_id = (getattr(reference, "project", None) or "").casefold()
        dataset_id = (getattr(reference, "dataset_id", None) or "").casefold()
        table_id = (getattr(reference, "table_id", None) or "").casefold()
        if project_id != personal_project:
            raise DependencyVerificationError("BigQuery reported a dependency outside the personal project.")
        if not dataset_id or not table_id or f"{dataset_id}.{table_id}" not in approved:
            raise DependencyVerificationError("BigQuery reported a dependency outside the approved view definitions.")
