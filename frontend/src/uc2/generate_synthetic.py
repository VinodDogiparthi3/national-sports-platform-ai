"""Generate deterministic frontend fixtures without importing UC1 or cloud clients."""

import json
import socket
from pathlib import Path
from unittest.mock import patch


def generate():
    from uc2.models import Application, ApplicationAnalysis, SummaryRequest, SummaryResponse
    from uc2.repository import SyntheticRepository
    from uc2.service import analyze, summarize

    repository = SyntheticRepository()
    months = ("2026-10", "2026-09")
    cutoffs = ("2026-11-01T00:00:00Z", "2026-10-10T09:00:00Z", "2026-10-03T09:00:00Z")
    snapshots = []
    for month in months:
        for cutoff in cutoffs:
            request = SummaryRequest(received_month=month, as_of=cutoff)
            summary = SummaryResponse.model_validate(summarize(request, repository).model_dump())
            analyses = {}
            workflows = {}
            for item in summary.applications:
                detail = ApplicationAnalysis.model_validate(analyze(item.application_id, request.as_of, repository).model_dump())
                workflow = Application.model_validate(repository.get_application(item.application_id, request.as_of).model_dump())
                analyses[item.application_id] = detail.model_dump(mode="json")
                workflows[item.application_id] = workflow.model_dump(mode="json")
            snapshots.append({
                "received_month": month, "as_of": cutoff,
                "summary": summary.model_dump(mode="json"),
                "analyses": analyses, "workflows": workflows,
                "presentation": {
                    "outlier_count": sum(item.outlier_status == "outlier" for item in summary.applications),
                    "maximum_elapsed_hours": max((item.elapsed_hours or 0 for item in summary.applications), default=0),
                },
            })
    return {
        "data_mode": "synthetic", "fixture_version": repository.fixture_version,
        "policy": repository.policy.model_dump(mode="json"),
        "months": months, "cutoffs": cutoffs, "snapshots": snapshots,
    }


if __name__ == "__main__":
    with patch.object(socket.socket, "connect", side_effect=AssertionError("Network forbidden")) as connect:
        with patch("socket.getaddrinfo", side_effect=AssertionError("DNS forbidden")) as dns:
            output = json.dumps(generate(), ensure_ascii=True, sort_keys=True, indent=2) + "\n"
            connect.assert_not_called()
            dns.assert_not_called()
    target = Path(__file__).with_name("syntheticResponses.json")
    target.write_text(output, encoding="utf-8")
    print(f"Generated {target.name}; validated synthetic contracts; zero network/DNS calls.")