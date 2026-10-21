import os
import subprocess
import sys
import unittest
from pathlib import Path

from pydantic import ValidationError

from schema_provider import SchemaColumn, SchemaNotFoundError, SchemaProvider, SourceSchema


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def run_config(overrides):
    environment = {
        key: value for key, value in os.environ.items()
        if key not in {
            "RUNTIME_PROFILE", "SCHEMA_PROVIDER", "LOCAL_SCHEMA_BACKUP_PATH", "GEMINI_PROJECT_ID",
            "GOOGLE_CLOUD_PROJECT", "BIGQUERY_PROJECT_ID", "BIGQUERY_DATASET_ID", "BIGQUERY_TABLE_ID",
            "CLIENT_GCP_PROJECT_ID", "PERSONAL_GCP_PROJECT_ID", "PERSONAL_GEMINI_PROJECT_ID",
            "PERSONAL_BIGQUERY_PROJECT_ID", "PERSONAL_GCP_GEMINI_CREDENTIALS_FILE",
            "PERSONAL_GCP_BIGQUERY_CREDENTIALS_FILE", "PERSONAL_GCP_ALLOWED_DATASETS",
            "PERSONAL_GCP_DEFAULT_DATASET_ID", "PERSONAL_GCP_DEFAULT_TABLE_ID",
            "PERSONAL_GCP_GEMINI_LOCATION", "PERSONAL_GCP_BIGQUERY_LOCATION",
        }
    }
    environment.update(overrides)
    return subprocess.run(
        [sys.executable, "-B", "-c", (
            "import config; "
            "print(config.SCHEMA_PROVIDER); "
            "print(config.LOCAL_SCHEMA_BACKUP_PATH or ''); "
            "print(config.PROJECT_ID); "
            "print(config.BIGQUERY_PROJECT_ID); "
            "print(config.GEMINI_PROJECT_ID); "
            "print(config.GEMINI_EFFECTIVE_PROJECT_ID); "
            "print(config.BIGQUERY_DATASET_ID); "
            "print(config.BIGQUERY_TABLE_ID)"
        )],
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
    )


class SchemaProviderConfigurationTests(unittest.TestCase):
    def test_default_provider_and_existing_gcp_defaults_are_preserved(self):
        result = run_config({})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [
            "CLIENT_GCP", "", "mos-gcp-host-dev-prj", "mos-gcp-host-dev-prj",
            "None", "mos-gcp-host-dev-prj", "dw_nsp_api_views", "nsp_base_view_01",
        ])

    def test_only_exact_provider_values_are_accepted(self):
        for value in ("client_gcp", "client-gcp", "LOCAL ", "OTHER", ""):
            with self.subTest(value=value):
                result = run_config({"SCHEMA_PROVIDER": value})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("SCHEMA_PROVIDER must be exactly", result.stderr)

    def test_local_requires_a_nonempty_backup_path_and_fails_closed(self):
        overrides = {"SCHEMA_PROVIDER": "LOCAL", "GEMINI_PROJECT_ID": "personal-project"}
        for path in (None, "", "   "):
            with self.subTest(path=path):
                settings = dict(overrides)
                if path is not None:
                    settings["LOCAL_SCHEMA_BACKUP_PATH"] = path
                result = run_config(settings)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("LOCAL_SCHEMA_BACKUP_PATH is required", result.stderr)

    def test_local_requires_an_explicit_nonempty_gemini_project(self):
        for project in (None, "", "   "):
            with self.subTest(project=project):
                settings = {"SCHEMA_PROVIDER": "LOCAL", "LOCAL_SCHEMA_BACKUP_PATH": "/approved/metadata"}
                if project is not None:
                    settings["GEMINI_PROJECT_ID"] = project
                result = run_config(settings)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("GEMINI_PROJECT_ID is required", result.stderr)

    def test_local_rejects_gemini_project_matching_either_client_project(self):
        for client_project in ("client-gemini-project", "client-bigquery-project"):
            with self.subTest(client_project=client_project):
                result = run_config({
                    "SCHEMA_PROVIDER": "LOCAL",
                    "LOCAL_SCHEMA_BACKUP_PATH": "/approved/metadata",
                    "GOOGLE_CLOUD_PROJECT": "client-gemini-project",
                    "BIGQUERY_PROJECT_ID": "client-bigquery-project",
                    "GEMINI_PROJECT_ID": client_project,
                })
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("must differ from the client GCP projects", result.stderr)

    def test_client_gcp_mode_does_not_require_a_local_backup_path(self):
        result = run_config({"SCHEMA_PROVIDER": "CLIENT_GCP"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines()[0:2], ["CLIENT_GCP", ""])

    def test_local_and_gemini_project_selection_are_independent(self):
        result = run_config({
            "SCHEMA_PROVIDER": "LOCAL",
            "LOCAL_SCHEMA_BACKUP_PATH": "/approved/metadata",
            "GOOGLE_CLOUD_PROJECT": "client-bigquery-project",
            "BIGQUERY_PROJECT_ID": "client-bigquery-project",
            "GEMINI_PROJECT_ID": "personal-gemini-project",
        })
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [
            "LOCAL", "/approved/metadata", "client-bigquery-project",
            "client-bigquery-project", "personal-gemini-project", "personal-gemini-project",
            "dw_nsp_api_views", "nsp_base_view_01",
        ])

    def test_client_gcp_ignores_optional_gemini_override_and_preserves_project_defaults(self):
        result = run_config({
            "GEMINI_PROJECT_ID": "personal-gemini-project",
            "GOOGLE_CLOUD_PROJECT": "client-project",
        })
        self.assertEqual(result.returncode, 0, result.stderr)
        values = result.stdout.splitlines()
        self.assertEqual(values[0], "CLIENT_GCP")
        self.assertEqual(values[2:6], ["client-project", "client-project", "personal-gemini-project", "client-project"])

    def test_gemini_client_initialization_uses_selected_project_and_existing_model_region(self):
        for provider, gemini_project, expected_project in (
            ("CLIENT_GCP", "personal-override-ignored", "client-project"),
            ("LOCAL", "authorized-personal-project", "authorized-personal-project"),
        ):
            with self.subTest(provider=provider):
                settings = {
                    "SCHEMA_PROVIDER": provider,
                    "GOOGLE_CLOUD_PROJECT": "client-project",
                    "BIGQUERY_PROJECT_ID": "client-data-project",
                    "GEMINI_PROJECT_ID": gemini_project,
                }
                if provider == "LOCAL":
                    settings["LOCAL_SCHEMA_BACKUP_PATH"] = "/approved/metadata"
                result = subprocess.run(
                    [sys.executable, "-B", "-c", (
                        "from unittest.mock import patch; import google.genai; "
                        "p=patch('google.genai.Client'); mock=p.start(); import gemini_service; "
                        "p.stop(); call=mock.call_args.kwargs; "
                        f"assert call['project']=={expected_project!r}; "
                        "assert call['vertexai'] is True; assert call['location']=='global'; "
                        "assert mock.call_count==1"
                    )],
                    cwd=REPOSITORY_ROOT,
                    env={**os.environ, **settings},
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class SourceSchemaContractTests(unittest.TestCase):
    def setUp(self):
        self.column = SchemaColumn(
            ordinal_position=1, name="record_id", data_type="STRING",
            nullability="REQUIRED", description="Stable source identifier.",
        )

    def test_normalizes_source_identity_and_column_metadata(self):
        schema = SourceSchema(
            project_id="approved-project", dataset_id="approved_dataset",
            object_name="approved_view", object_type="VIEW", columns=(self.column,),
            schema_retrieved_at="2026-10-21T03:00:00+03:00",
        )
        self.assertEqual(schema.source_id, "approved_dataset.approved_view")
        self.assertEqual(schema.full_id, "approved-project.approved_dataset.approved_view")
        self.assertEqual(schema.columns[0].ordinal_position, 1)
        self.assertEqual(schema.columns[0].nullability, "REQUIRED")
        self.assertEqual(schema.schema_retrieved_at.isoformat(), "2026-10-21T00:00:00+00:00")
        self.assertIsNone(schema.view_sql)

    def test_rejects_naive_schema_retrieval_timestamp(self):
        with self.assertRaises(ValidationError):
            SourceSchema(
                project_id="p", dataset_id="d", object_name="t", object_type="TABLE",
                schema_retrieved_at="2026-10-21T00:00:00", columns=(self.column,),
            )

    def test_rejects_invalid_column_modes_and_duplicate_names_or_positions(self):
        with self.assertRaises(ValidationError):
            SchemaColumn(ordinal_position=1, name="id", data_type="STRING", nullability="OPTIONAL")
        duplicate_name = self.column.model_copy(update={"ordinal_position": 2, "name": "RECORD_ID"})
        duplicate_position = self.column.model_copy(update={"name": "other_id"})
        for columns in ((self.column, duplicate_name), (self.column, duplicate_position)):
            with self.subTest(columns=columns), self.assertRaises(ValidationError):
                SourceSchema(
                    project_id="p", dataset_id="d", object_name="t",
                    object_type="TABLE", columns=columns,
                )

    def test_rejects_unmodeled_or_row_data_fields(self):
        with self.assertRaises(ValidationError):
            SourceSchema(
                project_id="p", dataset_id="d", object_name="t", object_type="TABLE",
                columns=(self.column,), rows=[{"record_id": "not schema metadata"}],
            )

    def test_protocol_defines_the_schema_only_provider_interface(self):
        class StubProvider:
            def get_schema(self, source_id: str) -> SourceSchema:
                self.source_id = source_id
                return SourceSchema(
                    project_id="p", dataset_id="d", object_name="t",
                    object_type="TABLE", columns=(SchemaColumn(
                        ordinal_position=1, name="id", data_type="STRING", nullability="NULLABLE",
                    ),),
                )

        provider: SchemaProvider = StubProvider()
        result = provider.get_schema("d.t")
        self.assertEqual(result.source_id, "d.t")
        self.assertFalse(hasattr(result, "rows"))
        with self.assertRaises(SchemaNotFoundError):
            raise SchemaNotFoundError("schema unavailable")


if __name__ == "__main__":
    unittest.main()