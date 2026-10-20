import subprocess
import sys
import unittest
from pathlib import Path


class IsolationTests(unittest.TestCase):
    def test_uc2_imports_and_analysis_do_not_import_uc1_or_use_network(self):
        script = """
import socket
import sys
from unittest.mock import patch
with patch.object(socket.socket, 'connect', side_effect=AssertionError('Network forbidden')) as connect:
    with patch('socket.getaddrinfo', side_effect=AssertionError('DNS forbidden')) as dns:
        from uc2.models import SummaryRequest
        from uc2.repository import SyntheticRepository
        from uc2.service import analyze, summarize
        request = SummaryRequest(received_month='2026-10', as_of='2026-11-01T00:00:00Z')
        repository = SyntheticRepository()
        assert summarize(request, repository).breached_count == 1
        assert analyze('SYN-D', request.as_of, repository).elapsed_hours == 600
        connect.assert_not_called()
        dns.assert_not_called()
        forbidden = {'app', 'config', 'bigquery_database', 'gemini_service', 'view_catalog', 'fastapi'}
        assert not forbidden.intersection(sys.modules)
        assert not any(name.startswith('google.cloud') or name.startswith('google.genai') for name in sys.modules)
"""
        result = subprocess.run(
            [sys.executable, "-B", "-c", script], cwd=Path(__file__).resolve().parents[2],
            capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)