"""Project-scoped BigQuery metadata discovery, separate from query authorization."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import re
import threading
import time

from google.cloud import bigquery

from bigquery_access import require_bigquery_access
from config import BIGQUERY_CATALOG_CACHE_TTL_SECONDS, BIGQUERY_PROJECT_ID, get_bigquery_client_kwargs


@dataclass(frozen=True)
class ColumnMetadata:
    name: str
    data_type: str
    mode: str
    description: str | None = None
    policy_tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class DiscoveredSource:
    project: str
    dataset: str
    name: str
    object_type: str
    description: str | None
    columns: tuple[ColumnMetadata, ...]
    view_definition: str | None = None
    time_partition_field: str | None = None
    range_partition_field: str | None = None
    clustering_fields: tuple[str, ...] = ()
    primary_key: tuple[str, ...] = ()
    foreign_keys: tuple[tuple[tuple[str, ...], str | None, tuple[str, ...]], ...] = ()
    metadata_error: str | None = None
    query_authorized: bool = False

    @property
    def source_id(self) -> str:
        return f"{self.dataset}.{self.name}"

    @property
    def full_id(self) -> str:
        return f"{self.project}.{self.dataset}.{self.name}"

    @property
    def display_name(self) -> str:
        return self.name

    @property
    def domain(self) -> str:
        return classify_domain(self.dataset, self.name, self.description or "", [column.name for column in self.columns])

    @property
    def schema_columns(self) -> list[tuple[str, str, str]]:
        return [(column.name, column.data_type, column.mode) for column in self.columns]


@dataclass(frozen=True)
class ProjectCatalog:
    project: str
    datasets: tuple[str, ...]
    sources: tuple[DiscoveredSource, ...]
    dataset_failures: tuple[tuple[str, str], ...] = ()
    object_failures: tuple[tuple[str, str, str], ...] = ()
    refreshed_at: float = 0.0


class CatalogUnavailableError(RuntimeError):
    pass


_lock = threading.Lock()
_catalog_cache: ProjectCatalog | None = None
_schema_cache: dict[str, tuple[float, tuple[ColumnMetadata, ...]]] = {}


def classify_domain(dataset: str, name: str, description: str, columns) -> str:
    text = " ".join([dataset, name, description, *columns]).casefold()
    rules = (
        ("Athletes", ("athlete", "player", "individual", "contactaffiliation")),
        ("Registrations", ("registration", "affiliation", "requestheader")),
        ("Sports", ("sport", "discipline")),
        ("Federations", ("federation",)),
        ("Licensing", ("license", "licence", "nafes", "permit")),
        ("Finance and budgets", ("budget", "expenditure", "finance", "cost", "billing", "price_index")),
        ("Facilities", ("facility", "reservation", "booking", "venue")),
        ("Events", ("event", "competition", "olympic", "medal")),
        ("Healthcare and sports medicine", ("health", "medical", "medicine", "injury", "obesity", "bmi")),
    )
    domains = [label for label, terms in rules if any(term in text for term in terms)]
    return ", ".join(domains) if domains else "Other discovered domains"


def _object_type(table) -> str:
    if getattr(table, "external_data_configuration", None):
        return "EXTERNAL_TABLE"
    if getattr(table, "materialized_view", None):
        return "MATERIALIZED_VIEW"
    if getattr(table, "snapshot_definition", None):
        return "SNAPSHOT"
    if getattr(table, "clone_definition", None):
        return "CLONE"
    if getattr(table, "biglake_configuration", None):
        return "BIGLAKE_TABLE"
    return getattr(table, "table_type", None) or "UNKNOWN"


def _column_metadata(field) -> ColumnMetadata:
    tags = getattr(field, "policy_tags", None)
    return ColumnMetadata(
        name=field.name,
        data_type=field.field_type,
        mode=field.mode,
        description=getattr(field, "description", None),
        policy_tags=tuple(getattr(tags, "names", None) or ()) if tags else (),
    )


def _fetch_object(client, dataset_id: str, item):
    require_bigquery_access("object metadata lookup")
    source_id = f"{dataset_id}.{item.table_id}"
    try:
        table = client.get_table(item.reference)
        schema = tuple(_column_metadata(field) for field in (table.schema or ()))
        constraints = getattr(table, "constraints", None)
        primary = getattr(constraints, "primary_key", None) if constraints else None
        foreign_keys = tuple(
            (
                tuple(getattr(foreign, "columns", None) or ()),
                str(getattr(foreign, "referenced_table", None)) if getattr(foreign, "referenced_table", None) else None,
                tuple(getattr(foreign, "referenced_columns", None) or ()),
            )
            for foreign in (getattr(constraints, "foreign_keys", None) or ())
        ) if constraints else ()
        return DiscoveredSource(
            project=BIGQUERY_PROJECT_ID,
            dataset=dataset_id,
            name=item.table_id,
            object_type=_object_type(table),
            description=getattr(table, "description", None),
            columns=schema,
            view_definition=getattr(table, "view_query", None),
            time_partition_field=getattr(getattr(table, "time_partitioning", None), "field", None),
            range_partition_field=getattr(getattr(table, "range_partitioning", None), "field", None),
            clustering_fields=tuple(getattr(table, "clustering_fields", None) or ()),
            primary_key=tuple(getattr(primary, "columns", None) or ()) if primary else (),
            foreign_keys=foreign_keys,
        )
    except Exception as error:
        return DiscoveredSource(
            project=BIGQUERY_PROJECT_ID,
            dataset=dataset_id,
            name=item.table_id,
            object_type=getattr(item, "table_type", None) or "UNKNOWN",
            description=None,
            columns=(),
            metadata_error=type(error).__name__,
        )


def discover_project_catalog(*, force_refresh: bool = False) -> ProjectCatalog:
    """Discover metadata across the configured project; never reads table rows or executes SQL."""
    require_bigquery_access("project catalog discovery")
    global _catalog_cache
    with _lock:
        cached = _catalog_cache
        if not force_refresh and cached and time.monotonic() - cached.refreshed_at < BIGQUERY_CATALOG_CACHE_TTL_SECONDS:
            return cached

    client = bigquery.Client(**get_bigquery_client_kwargs())
    try:
        datasets = sorted(client.list_datasets(project=BIGQUERY_PROJECT_ID), key=lambda dataset: dataset.dataset_id.casefold())
    except Exception as error:
        raise CatalogUnavailableError(f"Cannot list datasets for project {BIGQUERY_PROJECT_ID}.") from error

    dataset_objects = []
    dataset_failures = []
    for dataset in datasets:
        try:
            items = list(client.list_tables(dataset.reference))
            dataset_objects.extend((dataset.dataset_id, item) for item in items)
        except Exception as error:
            dataset_failures.append((dataset.dataset_id, type(error).__name__))

    sources = []
    object_failures = []
    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = [pool.submit(_fetch_object, client, dataset_id, item) for dataset_id, item in dataset_objects]
        for future in as_completed(futures):
            source = future.result()
            sources.append(source)
            if source.metadata_error:
                object_failures.append((source.dataset, source.name, source.metadata_error))
    sources.sort(key=lambda source: source.source_id.casefold())
    result = ProjectCatalog(
        project=BIGQUERY_PROJECT_ID,
        datasets=tuple(dataset.dataset_id for dataset in datasets),
        sources=tuple(sources),
        dataset_failures=tuple(dataset_failures),
        object_failures=tuple(object_failures),
        refreshed_at=time.monotonic(),
    )
    with _lock:
        _catalog_cache = result
        for source in sources:
            if not source.metadata_error:
                _schema_cache[source.full_id.casefold()] = (time.monotonic(), source.columns)
    return result


def get_discovered_source(source_id: str) -> DiscoveredSource | None:
    normalized = (source_id or "").casefold()
    return next((source for source in discover_project_catalog().sources if source.source_id.casefold() == normalized), None)


def get_discovered_schema(source: DiscoveredSource) -> tuple[ColumnMetadata, ...]:
    with _lock:
        cached = _schema_cache.get(source.full_id.casefold())
        if cached and time.monotonic() - cached[0] < BIGQUERY_CATALOG_CACHE_TTL_SECONDS:
            return cached[1]
    return source.columns


def clear_catalog_cache() -> None:
    global _catalog_cache
    with _lock:
        _catalog_cache = None
        _schema_cache.clear()


def sanitize_metadata_text(value: str | None, *, limit: int = 500) -> str:
    """Flatten and bound untrusted BigQuery metadata before it enters an LLM prompt."""
    if not value:
        return ""
    return re.sub(r"[\x00-\x1f\x7f]+", " ", value).strip()[:limit]
