#!/usr/bin/env bash
# Run the backend unittest suite with network and DNS blocked before any test module is imported.
#   OFFLINE_TESTS_REQUIRE_OS_ISOLATION=1  fail unless OS-level isolation is active (use in CI).
#   OFFLINE_TESTS_EXTERNAL_ISOLATION=1    caller already isolates the network (e.g. docker --network none).
# Extra arguments are passed to `python -m unittest discover`.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"

if [[ -z "${NSP_OFFLINE_STAGE:-}" ]]; then
    # Hide every ambient cloud identity and project before the isolated stage starts.
    while IFS='=' read -r name _; do
        case "$name" in
            PERSONAL_GCP_*|GOOGLE_*|CLOUDSDK_*|GCLOUD_*|GCE_METADATA_*|BIGQUERY_*|GEMINI_*|RUNTIME_PROFILE|SCHEMA_PROVIDER|CLIENT_GCP_PROJECT_ID|LOCAL_SCHEMA_BACKUP_PATH|SEMANTIC_SEARCH_ENABLED)
                unset "$name" ;;
        esac
    done < <(env)
    gcloud_config="$(mktemp -d)"
    trap 'rm -rf "$gcloud_config"' EXIT
    export CLOUDSDK_CONFIG="$gcloud_config"
    export NO_GCE_CHECK=True
    export SCHEMA_PROVIDER=CLIENT_GCP
    export GEMINI_PROJECT_ID=
    export PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1
    export PYTHONPATH="$ROOT/tests:$ROOT"

    if [[ "${OFFLINE_TESTS_EXTERNAL_ISOLATION:-0}" == 1 ]]; then
        export NSP_OFFLINE_STAGE=external
        set +e; bash "$0" "$@"; status=$?; set -e
    elif [[ "$(uname -s)" == Darwin && -x /usr/bin/sandbox-exec ]]; then
        export NSP_OFFLINE_STAGE=macos-sandbox
        set +e; /usr/bin/sandbox-exec -f "$ROOT/scripts/offline-sandbox.sb" /bin/bash "$0" "$@"; status=$?; set -e
    elif command -v unshare >/dev/null 2>&1 && unshare --net --map-root-user true 2>/dev/null; then
        export NSP_OFFLINE_STAGE=linux-netns
        set +e; unshare --net --map-root-user /bin/bash "$0" "$@"; status=$?; set -e
    elif [[ "${OFFLINE_TESTS_REQUIRE_OS_ISOLATION:-0}" == 1 ]]; then
        echo "OS-level network isolation is unavailable; refusing to run tests." >&2
        exit 2
    else
        echo "WARNING: OS-level network isolation unavailable; relying on the Python startup guard only." >&2
        export NSP_OFFLINE_STAGE=python-guard-only
        set +e; bash "$0" "$@"; status=$?; set -e
    fi
    exit "$status"
fi

cd "$ROOT"
echo "Offline test stage: $NSP_OFFLINE_STAGE"
if [[ "$NSP_OFFLINE_STAGE" != python-guard-only ]]; then
    "$PYTHON" -I -S "$ROOT/scripts/check_offline_isolation.py" os
fi
"$PYTHON" "$ROOT/scripts/check_offline_isolation.py" python
exec "$PYTHON" -m unittest discover -s tests -t . "$@"
