import unittest

from sql_sources import resolved_table_references


class ResolvedTableReferenceTests(unittest.TestCase):
    def test_resolves_tables_inside_ctes_and_nested_subqueries(self):
        sql = """
        WITH selected AS (
            SELECT id FROM `personal-project.approved_a.source_a`
        )
        SELECT selected.id
        FROM selected
        WHERE EXISTS (
            SELECT 1 FROM `personal-project.approved_b.source_b` AS nested
        )
        """
        sources = resolved_table_references(sql)
        self.assertEqual(
            {(table.catalog, table.db, table.name) for table in sources},
            {
                ("personal-project", "approved_a", "source_a"),
                ("personal-project", "approved_b", "source_b"),
            },
        )

    def test_deduplicates_repeated_physical_references(self):
        sources = resolved_table_references(
            "SELECT a.id FROM `personal-project.approved.source` a "
            "JOIN `personal-project.approved.source` b ON a.id=b.id"
        )
        self.assertEqual(len(sources), 1)

    def test_rejects_multiple_statements_and_non_select_roots(self):
        for sql in (
            "SELECT id FROM `personal-project.approved.source`; SELECT 1",
            "DELETE FROM `personal-project.approved.source`",
        ):
            with self.subTest(sql=sql), self.assertRaises(ValueError):
                resolved_table_references(sql)


if __name__ == "__main__":
    unittest.main()