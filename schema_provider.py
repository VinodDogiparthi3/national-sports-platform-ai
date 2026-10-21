from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
from typing import Annotated, Literal, Protocol

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from config import BIGQUERY_PROJECT_ID
from view_catalog import APPROVED_VIEWS, AUTHORIZED_TABLES


ObjectType = Literal["TABLE", "VIEW", "MATERIALIZED_VIEW", "EXTERNAL_TABLE"]
ColumnMode = Literal["NULLABLE", "REQUIRED", "REPEATED"]


def utc_timestamp(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Schema retrieval timestamps must include a timezone.")
    return value.astimezone(timezone.utc)


MetadataTimestamp = Annotated[datetime, AfterValidator(utc_timestamp)]


class SchemaColumn(BaseModel):
    """Normalized, row-free metadata for one source column."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ordinal_position: int = Field(ge=1)
    name: str = Field(min_length=1)
    data_type: str = Field(min_length=1)
    nullability: ColumnMode
    description: str | None = None


class SourceSchema(BaseModel):
    """Normalized schema metadata; contains no source rows or credentials."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    project_id: str = Field(min_length=1)
    dataset_id: str = Field(min_length=1)
    object_name: str = Field(min_length=1)
    object_type: ObjectType
    table_description: str | None = None
    schema_retrieved_at: MetadataTimestamp | None = None
    columns: tuple[SchemaColumn, ...]
    view_sql: str | None = None

    @model_validator(mode="after")
    def validate_columns(self):
        names = [column.name.casefold() for column in self.columns]
        positions = [column.ordinal_position for column in self.columns]
        if len(names) != len(set(names)):
            raise ValueError("Schema contains duplicate column names.")
        if len(positions) != len(set(positions)):
            raise ValueError("Schema contains duplicate column ordinal positions.")
        return self

    @property
    def source_id(self) -> str:
        return f"{self.dataset_id}.{self.object_name}"

    @property
    def full_id(self) -> str:
        return f"{self.project_id}.{self.source_id}"


class SchemaNotFoundError(LookupError):
    """Raised when the selected provider cannot supply an authorized source schema."""


class UnauthorizedSchemaSourceError(PermissionError):
    """Raised when a source ID is outside the checked-in UC1 authorization allowlists."""


class SchemaProvider(Protocol):
    """Read schema metadata only; implementations must never return table rows."""

    def get_schema(self, source_id: str) -> SourceSchema:
        """Return a schema for an already-authorized source or raise SchemaNotFoundError."""
        ...


class LocalBackupSchemaProvider:
    """Read verified schema JSON from an approved metadata-only local backup."""

    _CHECKSUM_LINE = re.compile(r"^([0-9a-f]{64})  ([^\r\n]+)$")

    def __init__(self, backup_path: str | os.PathLike[str], expected_project_id: str = BIGQUERY_PROJECT_ID):
        raw_root = Path(backup_path).expanduser()
        if not raw_root.is_absolute():
            raise ValueError("Local schema backup path must be absolute.")
        self._root = self._validate_root(raw_root)
        if not expected_project_id or not expected_project_id.strip():
            raise ValueError("Expected schema project ID must be nonempty.")
        self._expected_project_id = expected_project_id
        self._manifest = self._read_json("manifest.json")
        self._validate_manifest()
        self._checksums = self._read_checksums()
        self._verify_file_set_and_checksums()
        self._backup_identity = hashlib.sha256(self._safe_file("checksums.sha256").read_bytes()).hexdigest()
        self._schemas = self._load_schema_index()
        self._authorized = self._build_authorized_source_map()

    @staticmethod
    def _validate_root(root: Path) -> Path:
        if ".." in root.parts:
            raise ValueError("Local schema backup path must not contain traversal components.")
        current = Path(root.anchor)
        for component in root.parts[1:]:
            current = current / component
            try:
                metadata = current.lstat()
            except FileNotFoundError as error:
                raise ValueError("Local schema backup directory does not exist.") from error
            if current.is_symlink():
                raise ValueError("Symlinks are not allowed in the local schema backup path.")
            if current == root and not current.is_dir():
                raise ValueError("Local schema backup path must be a directory.")
            if current == root and metadata.st_uid != os.getuid():
                raise ValueError("Local schema backup must be owned by the current user.")
        return root

    def _safe_file(self, relative: str) -> Path:
        if not isinstance(relative, str) or not relative or "\\" in relative:
            raise ValueError("Backup manifest contains an invalid relative path.")
        parsed = PurePosixPath(relative)
        if parsed.is_absolute() or any(part in {"", ".", ".."} for part in parsed.parts):
            raise ValueError("Backup manifest contains a path traversal.")
        candidate = self._root.joinpath(*parsed.parts)
        current = self._root
        for component in parsed.parts:
            current = current / component
            try:
                current.lstat()
            except FileNotFoundError as error:
                raise ValueError("Backup manifest references a missing file.") from error
            if current.is_symlink():
                raise ValueError("Symlinks are not allowed in the local schema backup.")
        resolved = candidate.resolve(strict=True)
        try:
            resolved.relative_to(self._root)
        except ValueError as error:
            raise ValueError("Backup path escapes the configured root.") from error
        if not resolved.is_file():
            raise ValueError("Backup manifest path does not reference a file.")
        return resolved

    def _read_json(self, relative: str):
        path = self._safe_file(relative)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError(f"Invalid JSON in local schema backup file: {relative}.") from error

    def _validate_manifest(self) -> None:
        manifest = self._manifest
        if not isinstance(manifest, dict) or manifest.get("backup_format_version") != 1:
            raise ValueError("Unsupported local schema backup manifest.")
        created_at = manifest.get("created_at")
        try:
            created_timestamp = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except (AttributeError, TypeError, ValueError) as error:
            raise ValueError("Local schema backup manifest has an invalid creation timestamp.") from error
        if created_timestamp.tzinfo is None or created_timestamp.utcoffset() is None:
            raise ValueError("Local schema backup manifest timestamp must include a timezone.")
        if manifest.get("project_id") != self._expected_project_id:
            raise ValueError("Local schema backup project does not match configured BigQuery project.")
        if manifest.get("row_data_exported") is not False or manifest.get("query_results_exported") is not False:
            raise ValueError("Local schema backup is not marked metadata-only.")
        if manifest.get("credentials_exported") is not False:
            raise ValueError("Local schema backup must not contain credentials.")
        if manifest.get("failed_object_count") != 0 or manifest.get("failed_or_incompletely_listed_dataset_count") != 0:
            raise ValueError("Local schema backup manifest reports incomplete metadata export.")
        datasets = manifest.get("datasets")
        if not isinstance(datasets, list) or not datasets or not all(isinstance(item, str) and item for item in datasets):
            raise ValueError("Local schema backup manifest has an invalid dataset list.")
        if len(datasets) != len(set(datasets)):
            raise ValueError("Local schema backup manifest has duplicate datasets.")
        if manifest.get("status_report") != "export-status.json":
            raise ValueError("Local schema backup has an unsupported status report path.")
        count = manifest.get("exported_object_count")
        if not isinstance(count, int) or count < 0 or manifest.get("approved_object_count") != count:
            raise ValueError("Local schema backup manifest has inconsistent object counts.")

    @property
    def backup_identity(self) -> str:
        return self._backup_identity

    @property
    def backup_created_at(self) -> str:
        return self._manifest["created_at"]

    def _read_checksums(self) -> dict[str, str]:
        path = self._safe_file("checksums.sha256")
        result = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            match = self._CHECKSUM_LINE.fullmatch(line)
            if not match:
                raise ValueError("Local schema backup has an invalid checksum line.")
            digest, relative = match.groups()
            if relative == "checksums.sha256" or relative in result:
                raise ValueError("Local schema backup has a duplicate or self-referential checksum entry.")
            result[relative] = digest
        if not result:
            raise ValueError("Local schema backup checksum manifest is empty.")
        return result

    def _verify_file_set_and_checksums(self) -> None:
        actual = set()
        for directory, subdirectories, filenames in os.walk(self._root, followlinks=False):
            current = Path(directory)
            for name in subdirectories:
                if (current / name).is_symlink():
                    raise ValueError("Symlinks are not allowed in the local schema backup.")
            for name in filenames:
                path = current / name
                if path.is_symlink():
                    raise ValueError("Symlinks are not allowed in the local schema backup.")
                relative = path.relative_to(self._root).as_posix()
                if relative != "checksums.sha256":
                    actual.add(relative)
        if actual != set(self._checksums):
            raise ValueError("Local schema backup file set does not match its checksum manifest.")
        for relative, expected_digest in self._checksums.items():
            path = self._safe_file(relative)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != expected_digest:
                raise ValueError(f"Checksum verification failed for {relative}.")

    def _load_schema_index(self) -> dict[tuple[str, str], str]:
        status = self._read_json("export-status.json")
        if not isinstance(status, dict) or status.get("row_data_exported") is not False or status.get("credentials_exported") is not False:
            raise ValueError("Local schema backup status report is invalid or not metadata-only.")
        results = status.get("results")
        if not isinstance(results, list) or len(results) != self._manifest["exported_object_count"]:
            raise ValueError("Local schema backup status report has inconsistent object results.")
        index = {}
        for entry in results:
            if not isinstance(entry, dict) or entry.get("status") != "exported":
                raise ValueError("Local schema backup contains an unsuccessful object status.")
            dataset_id = entry.get("dataset_id")
            object_name = entry.get("object_name")
            relative = entry.get("schema_path")
            if dataset_id not in self._manifest["datasets"] or not isinstance(object_name, str) or not isinstance(relative, str):
                raise ValueError("Local schema backup contains an invalid object identity.")
            key = (dataset_id.casefold(), object_name.casefold())
            if key in index:
                raise ValueError("Local schema backup contains duplicate object identities.")
            if not relative.startswith("schemas/") or relative not in self._checksums:
                raise ValueError("Schema path is missing from the checksum manifest.")
            index[key] = relative
        return index

    @staticmethod
    def _build_authorized_source_map() -> dict[str, tuple[str, str, ObjectType]]:
        authorized: dict[str, tuple[str, str, ObjectType]] = {}
        for view in APPROVED_VIEWS:
            key = view.source_id.casefold()
            if key in authorized:
                raise RuntimeError("Duplicate source IDs in the approved view allowlist.")
            authorized[key] = (view.dataset, view.view, "VIEW")
        for dataset_id, table_names in AUTHORIZED_TABLES.items():
            for table_name in table_names:
                key = f"{dataset_id}.{table_name}".casefold()
                if key in authorized:
                    raise RuntimeError("Duplicate source IDs in checked-in authorization allowlists.")
                authorized[key] = (dataset_id, table_name, "TABLE")
        return authorized

    def get_schema(self, source_id: str) -> SourceSchema:
        if not isinstance(source_id, str) or not source_id.strip():
            raise UnauthorizedSchemaSourceError("Source ID is not in the checked-in authorization allowlists.")
        identity = self._authorized.get(source_id.casefold())
        if identity is None:
            raise UnauthorizedSchemaSourceError("Source ID is not in the checked-in authorization allowlists.")
        dataset_id, object_name, expected_type = identity
        relative = self._schemas.get((dataset_id.casefold(), object_name.casefold()))
        if relative is None:
            raise SchemaNotFoundError(f"No local schema is available for authorized source {source_id}.")
        path = self._safe_file(relative)
        if hashlib.sha256(path.read_bytes()).hexdigest() != self._checksums[relative]:
            raise ValueError(f"Checksum verification failed for {relative}.")
        raw = self._read_json(relative)
        if not isinstance(raw, dict):
            raise ValueError("Local schema document must be a JSON object.")
        backup_fields = {
            "project_id", "dataset_id", "object_name", "source_kind", "object_type",
            "table_description", "schema_retrieved_at", "columns", "view_sql", "view_sql_status",
        }
        if set(raw) - backup_fields:
            raise ValueError("Local schema document contains unexpected backup fields.")
        source_kind = raw.get("source_kind")
        if source_kind not in {"approved_view", "authorized_table", "approved_dataset_object", "uc2_candidate"}:
            raise ValueError("Local schema document has an unknown backup source kind.")
        if "view_sql_status" in raw:
            allowed_view_statuses = {"available", "unavailable", "omitted_not_in_approved_view_catalog"}
            if raw["view_sql_status"] not in allowed_view_statuses:
                raise ValueError("Local schema document has an invalid view SQL status.")
            if raw["view_sql_status"] == "available" and not isinstance(raw.get("view_sql"), str):
                raise ValueError("Local schema document has inconsistent view SQL metadata.")
            if raw["view_sql_status"] != "available" and raw.get("view_sql") is not None:
                raise ValueError("Local schema document contains unexpected view SQL.")
        normalized = {key: raw[key] for key in (
            "project_id", "dataset_id", "object_name", "object_type", "table_description",
            "schema_retrieved_at", "columns", "view_sql",
        ) if key in raw}
        schema = SourceSchema.model_validate(normalized)
        if (
            schema.project_id != self._expected_project_id
            or schema.dataset_id.casefold() != dataset_id.casefold()
            or schema.object_name.casefold() != object_name.casefold()
            or schema.object_type != expected_type
        ):
            raise ValueError("Local schema identity does not match the authorized source.")
        return schema