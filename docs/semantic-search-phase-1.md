# Phase 1: local semantic retrieval

`SEMANTIC_SEARCH_ENABLED=true` opts in; the default is `false`. Other values
are rejected at startup. This is independent of the runtime profile: **local**
describes the retrieval operation, not a new execution or authorization mode.

The approved Phase 1 corpus is the small hand-authored synthetic vocabulary in
`semantic_search.py`. It contains generic business descriptions associated with
existing source IDs, including the personal synthetic athlete source. It does
not read backups, client files, discovered metadata, credentials, schemas, stored
values, or row data. No external index, embedding API, model, model download,
dependency, or persistent query log is used.

Retrieval uses bilingual English/Arabic synonym normalization and TF-IDF cosine
similarity. This is an intentionally limited, deterministic semantic/lexical
baseline, not neural embeddings or a general language-understanding model.
It returns at most three matches scoring at least 0.15, with a stable
document-ID tie break and versioned provenance. Scores are not probabilities.
Unknown vocabulary or an empty authorized intersection returns no hints.

Routing intersects matches with its existing capability-filtered authorized
source list. It retains that **entire** list, so a missing/low-scoring synthetic
match never removes a source. SQL generation retrieves only for the selected
source, after loading the authoritative schema. Both prompts label hints as
untrusted advisory data; hints cannot grant access, define metrics, supply
columns/filter values, settle reporting years, or override business rules.
Pending source/year/detail selections, capability checks, filter validation,
population row semantics, SQL guardrails and preview-only contracts are unchanged.
Disabled mode adds no prompt text and performs no retrieval.

This feature is not an offline replacement for the application's existing
Gemini router/SQL generator. Those paths retain their existing runtime access
controls. Enabling retrieval does not enable or disable cloud execution.
For offline development and validation, mock model responses and use the
repository's network-isolated runner; never start a live query to test retrieval.
Integration fixtures mock `client.models.generate_content`, the shared boundary
for lazy and disabled clients, rather than the lazy-only `_get_client` helper.
The same integration cases also run with a disabled client. A separate safeguard
test verifies that removing the mock restores the PERSONAL_GCP denial with
execution disabled, without constructing an SDK client or loading credentials.
The semantic suite also starts a subprocess in the actual disabled PERSONAL_GCP
profile, using temporary dummy credential files only for configuration validation.
Schema business-rule tests use an in-memory synthetic LOCAL provider, never the
guarded CLIENT_GCP metadata branch. HTTP tests use loopback for allowed requests:
LOCAL previews succeed, while disabled PERSONAL_GCP returns HTTP 503 for `/ask`
and an SSE error for `/ask/stream`. Non-loopback requests remain HTTP 403, and
the subprocess verifies that the BigQuery schema-access guard still denies access.

Run the deterministic feature tests with:

```sh
bash scripts/run_offline_tests.sh -p test_semantic_search.py
```

Phase 1 is deliberately limited to advisory prompt augmentation. No new API,
UI, automatic source selection, SQL templates, joins, or expanded allowlists
are introduced.

See the [offline benchmark](semantic-search-benchmark.md) for the fixed
40-question English/Arabic evaluation, enabled/disabled comparisons, actual
metrics, and single-source limitations.
