import unittest
from types import SimpleNamespace
from unittest.mock import patch

from bigquery_catalog import _object_type, classify_domain, clear_catalog_cache, discover_project_catalog
from config import BIGQUERY_CATALOG_CACHE_TTL_SECONDS
from bigquery_catalog import CatalogUnavailableError, ColumnMetadata, DiscoveredSource
from view_catalog import APPROVED_VIEWS, get_dynamic_authorized_sources, get_queryable_sources


class MetadataCatalogTests(unittest.TestCase):
    def setUp(self):
        clear_catalog_cache()
        self.addCleanup(clear_catalog_cache)

    def test_discovers_project_datasets_and_object_metadata_without_queries(self):
        datasets = [
            SimpleNamespace(dataset_id="dw_sports", reference="dw_sports"),
            SimpleNamespace(dataset_id="dw_finance", reference="dw_finance"),
        ]
        items_by_dataset = {
            "dw_sports": [SimpleNamespace(table_id="results", table_type="TABLE", reference="p.dw_sports.results")],
            "dw_finance": [SimpleNamespace(table_id="budget_view", table_type="VIEW", reference="p.dw_finance.budget_view")],
        }
        fields = [
            SimpleNamespace(name="event_id", field_type="STRING", mode="REQUIRED", description="Event identifier", policy_tags=None),
            SimpleNamespace(name="amount", field_type="NUMERIC", mode="NULLABLE", description=None, policy_tags=None),
        ]
        tables = {
            "p.dw_sports.results": SimpleNamespace(
                table_type="TABLE", schema=fields, description="Sports results", view_query=None,
                time_partitioning=SimpleNamespace(field="event_date"), range_partitioning=None,
                clustering_fields=["event_id"], external_data_configuration=None,
                materialized_view=None, snapshot_definition=None, clone_definition=None,
                biglake_configuration=None,
                constraints=SimpleNamespace(
                    primary_key=SimpleNamespace(columns=["event_id"]),
                    foreign_keys=[SimpleNamespace(
                        columns=["league_id"],
                        referenced_table="p.dw_sports.leagues",
                        referenced_columns=["league_id"],
                    )],
                ),
            ),
            "p.dw_finance.budget_view": SimpleNamespace(
                table_type="VIEW", schema=fields, description="Budget reporting", view_query="SELECT ...",
                time_partitioning=None, range_partitioning=None, clustering_fields=None,
                external_data_configuration=None, materialized_view=None,
                snapshot_definition=None, clone_definition=None, biglake_configuration=None,
                constraints=None,
            ),
        }
        with patch("bigquery_catalog.bigquery.Client") as client_class:
            client = client_class.return_value
            client.list_datasets.return_value = datasets
            client.list_tables.side_effect = lambda reference: items_by_dataset[reference]
            client.get_table.side_effect = lambda reference: tables[reference]

            catalog = discover_project_catalog(force_refresh=True)

        self.assertEqual(catalog.datasets, ("dw_finance", "dw_sports"))
        self.assertEqual({source.object_type for source in catalog.sources}, {"TABLE", "VIEW"})
        sports = next(source for source in catalog.sources if source.name == "results")
        self.assertEqual(sports.schema_columns[0], ("event_id", "STRING", "REQUIRED"))
        self.assertEqual(sports.primary_key, ("event_id",))
        self.assertEqual(sports.foreign_keys, ((("league_id",), "p.dw_sports.leagues", ("league_id",)),))
        self.assertEqual(sports.time_partition_field, "event_date")
        self.assertEqual(sports.clustering_fields, ("event_id",))
        view = next(source for source in catalog.sources if source.name == "budget_view")
        self.assertEqual(view.view_definition, "SELECT ...")
        client.query.assert_not_called()

    def test_discovery_visibility_alone_does_not_authorize_source(self):
        source = DiscoveredSource(
            project="mos-gcp-host-dev-prj",
            dataset="dw_sharepoint",
            name="new_budget_table",
            object_type="TABLE",
            description="Budget source",
            columns=(ColumnMetadata("project", "STRING", "NULLABLE"),),
        )
        with (
            patch("view_catalog.BIGQUERY_DYNAMIC_ALLOWED_DATASETS", frozenset()),
            patch("view_catalog.BIGQUERY_DYNAMIC_SOURCE_COLUMN_POLICIES", {}),
            patch("view_catalog.get_discovered_sources", return_value=(source,)),
        ):
            self.assertEqual(get_dynamic_authorized_sources(), ())

    def test_dynamic_policy_authorizes_only_explicit_dataset_object_columns(self):
        source = DiscoveredSource(
            project="mos-gcp-host-dev-prj",
            dataset="dw_sharepoint",
            name="new_budget_table",
            object_type="TABLE",
            description="Budget source",
            columns=(
                ColumnMetadata("project", "STRING", "NULLABLE"),
                ColumnMetadata("amount", "NUMERIC", "NULLABLE"),
                ColumnMetadata("owner_email", "STRING", "NULLABLE", policy_tags=("sensitive",)),
            ),
        )
        policies = {"dw_sharepoint.new_budget_table": ["project", "amount", "owner_email"]}
        with (
            patch("view_catalog.BIGQUERY_DYNAMIC_ALLOWED_DATASETS", frozenset({"dw_sharepoint"})),
            patch("view_catalog.BIGQUERY_DYNAMIC_SOURCE_COLUMN_POLICIES", policies),
            patch("view_catalog.get_discovered_sources", return_value=(source,)),
        ):
            authorized = get_dynamic_authorized_sources()

        self.assertEqual(len(authorized), 1)
        self.assertTrue(authorized[0].query_authorized)
        self.assertEqual([column.name for column in authorized[0].columns], ["project", "amount"])
        self.assertEqual([column.name for column in source.columns], ["project", "amount", "owner_email"])

    def test_dynamic_routing_disabled_denies_dynamic_sources(self):
        source = DiscoveredSource(
            project="mos-gcp-host-dev-prj",
            dataset="dw_sharepoint",
            name="new_budget_table",
            object_type="TABLE",
            description=None,
            columns=(ColumnMetadata("amount", "NUMERIC", "NULLABLE"),),
        )
        with (
            patch("view_catalog.BIGQUERY_DYNAMIC_ROUTING_ENABLED", False),
            patch("view_catalog.BIGQUERY_DYNAMIC_CATALOG_ENABLED", True),
            patch("view_catalog.BIGQUERY_DYNAMIC_ALLOWED_DATASETS", frozenset({"dw_sharepoint"})),
            patch("view_catalog.BIGQUERY_DYNAMIC_SOURCE_COLUMN_POLICIES", {source.source_id: ["amount"]}),
            patch("view_catalog.get_discovered_sources", return_value=(source,)) as discover,
        ):
            self.assertEqual(get_dynamic_authorized_sources(), ())
        discover.assert_not_called()

    def test_catalog_failure_falls_back_to_static_approved_views(self):
        with (
            patch("view_catalog.BIGQUERY_DYNAMIC_ALLOWED_DATASETS", frozenset({"dw_sharepoint"})),
            patch("view_catalog.BIGQUERY_DYNAMIC_SOURCE_COLUMN_POLICIES", {"dw_sharepoint.some_view": ["value"]}),
            patch("view_catalog.get_authorized_tables", return_value=()),
            patch("view_catalog.get_discovered_sources", side_effect=CatalogUnavailableError("metadata unavailable")),
        ):
            sources = get_queryable_sources()

        self.assertEqual(sources, APPROVED_VIEWS)
    def test_catalog_is_cached_until_refresh_or_ttl_expiration(self):
        with patch("bigquery_catalog.bigquery.Client") as client_class:
            client_class.return_value.list_datasets.return_value = []

            first = discover_project_catalog()
            second = discover_project_catalog()
            refreshed = discover_project_catalog(force_refresh=True)

        self.assertIs(first, second)
        self.assertIsNot(first, refreshed)
        self.assertEqual(client_class.return_value.list_datasets.call_count, 2)
        client_class.return_value.query.assert_not_called()

    def test_catalog_refreshes_after_configured_ttl(self):
        with patch("bigquery_catalog.bigquery.Client") as client_class:
            client_class.return_value.list_datasets.return_value = []
            with patch("bigquery_catalog.time.monotonic", return_value=100):
                first = discover_project_catalog(force_refresh=True)
            with patch("bigquery_catalog.time.monotonic", return_value=101):
                cached = discover_project_catalog()
            with patch("bigquery_catalog.time.monotonic", return_value=101 + BIGQUERY_CATALOG_CACHE_TTL_SECONDS + 1):
                refreshed = discover_project_catalog()

        self.assertIs(first, cached)
        self.assertIsNot(cached, refreshed)
        self.assertEqual(client_class.return_value.list_datasets.call_count, 2)
        client_class.return_value.query.assert_not_called()

    def test_object_types_distinguish_external_materialized_snapshot_and_clone(self):
        cases = (
            (SimpleNamespace(external_data_configuration=object()), "EXTERNAL_TABLE"),
            (SimpleNamespace(materialized_view=object()), "MATERIALIZED_VIEW"),
            (SimpleNamespace(snapshot_definition=object()), "SNAPSHOT"),
            (SimpleNamespace(clone_definition=object()), "CLONE"),
            (SimpleNamespace(table_type="VIEW"), "VIEW"),
            (SimpleNamespace(table_type="TABLE"), "TABLE"),
        )
        for table, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(_object_type(table), expected)

    def test_domain_classification_handles_business_terms_and_fallback(self):
        self.assertIn("Finance and budgets", classify_domain("dw_sharepoint", "budget", "", ["project"]))
        self.assertIn("Sports", classify_domain("misc", "registrations", "", ["sport_id"]))
        self.assertEqual(classify_domain("misc", "object", "", ["payload"]), "Other discovered domains")

    def test_metadata_failure_is_recorded_not_authorized(self):
        dataset = SimpleNamespace(dataset_id="dw_sports", reference="dw_sports")
        item = SimpleNamespace(table_id="broken", table_type="TABLE", reference="p.dw_sports.broken")
        with patch("bigquery_catalog.bigquery.Client") as client_class:
            client = client_class.return_value
            client.list_datasets.return_value = [dataset]
            client.list_tables.return_value = [item]
            client.get_table.side_effect = PermissionError("metadata denied")

            catalog = discover_project_catalog(force_refresh=True)

        self.assertEqual(catalog.object_failures, (("dw_sports", "broken", "PermissionError"),))
        self.assertEqual(catalog.sources[0].columns, ())
        client.query.assert_not_called()

    def test_catalog_handles_project_inventory_scale_without_authorizing_objects(self):
        clear_catalog_cache()
        self.addCleanup(clear_catalog_cache)
        datasets = [SimpleNamespace(dataset_id=f"dataset_{index}", reference=f"dataset_{index}") for index in range(55)]
        items_by_dataset = {}
        tables = {}
        remaining_tables = 721
        for dataset_index, dataset in enumerate(datasets):
            object_count = 16 if dataset_index < 48 else 15
            items = []
            for object_index in range(object_count):
                kind = "TABLE" if remaining_tables else "VIEW"
                if kind == "TABLE":
                    remaining_tables -= 1
                table_id = f"object_{object_index}"
                reference = f"p.{dataset.dataset_id}.{table_id}"
                item = SimpleNamespace(table_id=table_id, table_type=kind, reference=reference)
                items.append(item)
                tables[reference] = SimpleNamespace(
                    table_type=kind,
                    schema=[],
                    description=None,
                    view_query=None,
                    time_partitioning=None,
                    range_partitioning=None,
                    clustering_fields=None,
                    external_data_configuration=None,
                    materialized_view=None,
                    snapshot_definition=None,
                    clone_definition=None,
                    biglake_configuration=None,
                    constraints=None,
                )
            items_by_dataset[dataset.dataset_id] = items

        with patch("bigquery_catalog.bigquery.Client") as client_class:
            client = client_class.return_value
            client.list_datasets.return_value = datasets
            client.list_tables.side_effect = lambda reference: items_by_dataset[reference]
            client.get_table.side_effect = lambda reference: tables[reference]

            catalog = discover_project_catalog(force_refresh=True)

        self.assertEqual(len(catalog.datasets), 55)
        self.assertEqual(len(catalog.sources), 873)
        self.assertEqual(sum(source.object_type == "TABLE" for source in catalog.sources), 721)
        self.assertEqual(sum(source.object_type == "VIEW" for source in catalog.sources), 152)
        client.query.assert_not_called()


if __name__ == "__main__":
    unittest.main()
