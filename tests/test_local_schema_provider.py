import hashlib
import json
import os
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from schema_provider import (
    LocalBackupSchemaProvider,
    SchemaNotFoundError,
    UnauthorizedSchemaSourceError,
)


PROJECT = "mos-gcp-host-dev-prj"
APPROVED = ("dw_nsp_api", "ldv_clubs")
UNAPPROVED = ("dl_ms_crm_onprem", "ntw_licenserequestBase")


class LocalBackupSchemaProviderTests(unittest.TestCase):
    def make_backup(self, root, objects=None):
        objects = objects or {APPROVED: "TABLE"}
        root.mkdir(parents=True)
        inventory_objects = []
        status_results = []
        schema_paths = {}
        for (dataset_id, object_name), object_type in objects.items():
            relative = f"schemas/{dataset_id}/{object_name}.json"
            schema_paths[(dataset_id, object_name)] = relative
            document = {
                "project_id": PROJECT,
                "dataset_id": dataset_id,
                "object_name": object_name,
                "source_kind": "approved_view" if object_type == "VIEW" else "approved_dataset_object",
                "object_type": object_type,
                "table_description": "Synthetic metadata fixture.",
                "schema_retrieved_at": "2026-10-20T12:00:00Z",
                **({"view_sql": "SELECT record_id FROM synthetic_fixture", "view_sql_status": "available"} if object_type == "VIEW" else {}),
                "columns": [{
                    "ordinal_position": 1, "name": "record_id", "data_type": "STRING",
                    "nullability": "REQUIRED", "description": "Synthetic identifier.",
                }],
            }
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")
            inventory_objects.append({"object_name": object_name, "object_type": object_type, "status": "exported", "column_count": 1, "schema_path": relative})
            status_results.append({"dataset_id": dataset_id, "object_name": object_name, "source_kind": "approved_dataset_object", "status": "exported", "object_type": object_type, "column_count": 1, "schema_path": relative})

        datasets = sorted({dataset_id for dataset_id, _ in objects})
        inventories = root / "inventories"
        inventories.mkdir()
        for dataset_id in datasets:
            (inventories / f"{dataset_id}.json").write_text(json.dumps({
                "project_id": PROJECT, "dataset_id": dataset_id,
                "dataset_metadata_status": "available",
                "objects": [item for item in inventory_objects if item["object_name"] in {name for dataset, name in objects if dataset == dataset_id}],
            }, sort_keys=True), encoding="utf-8")

        status = {
            "expected_object_count": len(objects), "exported_object_count": len(objects),
            "failed_object_count": 0, "failed_or_incompletely_listed_dataset_count": 0,
            "results": status_results, "dataset_results": {dataset: {"status": "available"} for dataset in datasets},
            "row_data_exported": False, "credentials_exported": False,
        }
        (root / "export-status.json").write_text(json.dumps(status, sort_keys=True), encoding="utf-8")
        manifest = {
            "backup_format_version": 1, "project_id": PROJECT, "created_at": "2026-10-20T12:00:00Z",
            "destination_encryption": "test-only", "retention": "test-only", "datasets": datasets,
            "approved_dataset_count": len(datasets), "approved_object_count": len(objects),
            "exported_object_count": len(objects), "failed_object_count": 0,
            "failed_or_incompletely_listed_dataset_count": 0,
            "metadata_categories": ["schema"], "row_data_exported": False,
            "query_results_exported": False, "credentials_exported": False,
            "uc2_candidate_objects": [], "approved_view_sql_objects": [], "view_sql_omitted_objects": [],
            "status_report": "export-status.json",
        }
        (root / "manifest.json").write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
        files = [path for path in root.rglob("*") if path.is_file() and path.name != "checksums.sha256"]
        checksums = [f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(root).as_posix()}" for path in sorted(files)]
        (root / "checksums.sha256").write_text("\n".join(checksums) + "\n", encoding="utf-8")
        return schema_paths

    def test_loads_authorized_schema_without_network_or_bigquery(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve() / "backup"
            self.make_backup(root)
            before = {path.relative_to(root): hashlib.sha256(path.read_bytes()).hexdigest() for path in root.rglob("*") if path.is_file()}
            with (
                patch.object(socket.socket, "connect", side_effect=AssertionError("network forbidden")) as connect,
                patch("socket.getaddrinfo", side_effect=AssertionError("DNS forbidden")) as dns,
                patch("view_catalog.bigquery.Client") as bigquery_client,
                patch("bigquery_catalog.bigquery.Client") as catalog_client,
            ):
                provider = LocalBackupSchemaProvider(root, expected_project_id=PROJECT)
                schema = provider.get_schema("dw_nsp_api.ldv_clubs")
                connect.assert_not_called()
                dns.assert_not_called()
                bigquery_client.assert_not_called()
                catalog_client.assert_not_called()
            self.assertEqual(schema.full_id, f"{PROJECT}.dw_nsp_api.ldv_clubs")
            self.assertEqual(schema.columns[0].name, "record_id")
            self.assertEqual(provider.backup_created_at, "2026-10-20T12:00:00Z")
            self.assertRegex(provider.backup_identity, r"^[0-9a-f]{64}$")
            after = {path.relative_to(root): hashlib.sha256(path.read_bytes()).hexdigest() for path in root.rglob("*") if path.is_file()}
            self.assertEqual(before, after)

    def test_schema_metadata_selects_local_provider_without_live_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve() / "backup"
            self.make_backup(root, {("dw_nsp_api_views", "nsp_base_view_01"): "VIEW"})
            with (
                patch("schema_metadata.SCHEMA_PROVIDER", "LOCAL"),
                patch("schema_metadata.LOCAL_SCHEMA_BACKUP_PATH", str(root)),
                patch("schema_metadata.get_source", side_effect=__import__("view_catalog").get_source),
                patch("schema_metadata.bigquery.Client") as bigquery_client,
                patch("schema_metadata.get_discovered_source", side_effect=AssertionError("live catalog fallback")),
            ):
                from schema_metadata import clear_schema_cache, get_view_columns
                clear_schema_cache()
                self.addCleanup(clear_schema_cache)
                columns = get_view_columns("nsp_base_view_01")
            self.assertEqual(columns, [("record_id", "STRING", "REQUIRED")])
            bigquery_client.assert_not_called()

    def test_missing_local_backup_never_falls_back_to_bigquery(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory).resolve() / "missing-backup"
            with (
                patch("schema_metadata.SCHEMA_PROVIDER", "LOCAL"),
                patch("schema_metadata.LOCAL_SCHEMA_BACKUP_PATH", str(missing)),
                patch("schema_metadata.bigquery.Client") as bigquery_client,
            ):
                from schema_metadata import clear_schema_cache, get_view_columns
                clear_schema_cache()
                self.addCleanup(clear_schema_cache)
                with self.assertRaises(ValueError):
                    get_view_columns("nsp_base_view_01")
            bigquery_client.assert_not_called()

    def test_local_filter_values_are_unavailable_and_schema_context_says_not_to_guess(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve() / "backup"
            self.make_backup(root, {("dw_nsp_api_views", "nsp_base_view_01"): "VIEW"})
            with (
                patch("schema_metadata.SCHEMA_PROVIDER", "LOCAL"),
                patch("schema_metadata.LOCAL_SCHEMA_BACKUP_PATH", str(root)),
                patch("schema_metadata.bigquery.Client") as bigquery_client,
            ):
                from schema_metadata import clear_schema_cache, get_bigquery_schema, get_filter_values
                clear_schema_cache()
                self.addCleanup(clear_schema_cache)
                self.assertIsNone(get_filter_values("nsp_base_view_01"))
                context = get_bigquery_schema("nsp_base_view_01")
            self.assertIn("Stored filter values are unavailable in LOCAL schema mode", context)
            self.assertIn("Do not invent, translate, or guess filter values", context)
            bigquery_client.assert_not_called()

    def test_missing_authorized_schema_raises_not_found(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve() / "backup"
            self.make_backup(root)
            provider = LocalBackupSchemaProvider(root, expected_project_id=PROJECT)
            with self.assertRaises(SchemaNotFoundError):
                provider.get_schema("dw_nsp_api.ldv_federations")

    def test_unapproved_uc2_candidate_is_denied_even_if_present_in_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve() / "backup"
            self.make_backup(root, {APPROVED: "TABLE", UNAPPROVED: "TABLE"})
            provider = LocalBackupSchemaProvider(root, expected_project_id=PROJECT)
            with self.assertRaises(UnauthorizedSchemaSourceError):
                provider.get_schema("dl_ms_crm_onprem.ntw_licenserequestBase")

    def test_rejects_corrupt_or_mismatched_manifests(self):
        for mutation in ("json", "project", "counts", "failed", "row_data"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve() / "backup"
                self.make_backup(root)
                manifest_path = root / "manifest.json"
                if mutation == "json":
                    manifest_path.write_text("{invalid", encoding="utf-8")
                else:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    if mutation == "project":
                        manifest["project_id"] = "other-project"
                    elif mutation == "counts":
                        manifest["exported_object_count"] = 2
                    elif mutation == "failed":
                        manifest["failed_object_count"] = 1
                    else:
                        manifest["row_data_exported"] = True
                    manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
                with self.assertRaises(ValueError):
                    LocalBackupSchemaProvider(root, expected_project_id=PROJECT)

    def test_checksum_failure_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve() / "backup"
            paths = self.make_backup(root)
            schema_path = root / paths[APPROVED]
            schema_path.write_text(schema_path.read_text(encoding="utf-8") + " ", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Checksum verification failed"):
                LocalBackupSchemaProvider(root, expected_project_id=PROJECT)

    def test_mismatched_schema_identity_and_column_structure_are_rejected(self):
        for mutation in ("identity", "column"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve() / "backup"
                paths = self.make_backup(root)
                relative = paths[APPROVED]
                schema_path = root / relative
                schema = json.loads(schema_path.read_text(encoding="utf-8"))
                if mutation == "identity":
                    schema["object_name"] = "ntw_federations"
                else:
                    schema["columns"][0]["extra_row_data"] = "must be rejected"
                schema_path.write_text(json.dumps(schema, sort_keys=True), encoding="utf-8")
                checksums = root / "checksums.sha256"
                lines = []
                for line in checksums.read_text(encoding="utf-8").splitlines():
                    _, name = line.split("  ", 1)
                    digest = hashlib.sha256((root / name).read_bytes()).hexdigest()
                    lines.append(f"{digest}  {name}")
                checksums.write_text("\n".join(lines) + "\n", encoding="utf-8")
                provider = LocalBackupSchemaProvider(root, expected_project_id=PROJECT)
                with self.assertRaises(ValueError):
                    provider.get_schema("dw_nsp_api.ldv_clubs")

    def test_path_traversal_and_symlink_escape_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve() / "backup"
            paths = self.make_backup(root)
            status_path = root / "export-status.json"
            status = json.loads(status_path.read_text(encoding="utf-8"))
            status["results"][0]["schema_path"] = "schemas/../../outside.json"
            status_path.write_text(json.dumps(status, sort_keys=True), encoding="utf-8")
            checksums = root / "checksums.sha256"
            lines = []
            for line in checksums.read_text(encoding="utf-8").splitlines():
                _, name = line.split("  ", 1)
                lines.append(f"{hashlib.sha256((root / name).read_bytes()).hexdigest()}  {name}")
            checksums.write_text("\n".join(lines) + "\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                LocalBackupSchemaProvider(root, expected_project_id=PROJECT)

        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory).resolve()
            root = parent / "backup"
            paths = self.make_backup(root)
            schema = root / paths[APPROVED]
            outside = parent / "outside.json"
            outside.write_text(schema.read_text(encoding="utf-8"), encoding="utf-8")
            schema.unlink()
            schema.symlink_to(outside)
            with self.assertRaisesRegex(ValueError, "Symlinks are not allowed"):
                LocalBackupSchemaProvider(root, expected_project_id=PROJECT)

    def test_rejects_symlinked_backup_root_and_relative_root_path(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory).resolve()
            actual = parent / "actual"
            self.make_backup(actual)
            link = parent / "linked"
            link.symlink_to(actual, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "Symlinks are not allowed"):
                LocalBackupSchemaProvider(link, expected_project_id=PROJECT)
            with self.assertRaisesRegex(ValueError, "absolute"):
                LocalBackupSchemaProvider("relative/backup", expected_project_id=PROJECT)


if __name__ == "__main__":
    unittest.main()