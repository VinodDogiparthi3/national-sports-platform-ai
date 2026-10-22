"""Preflight checks for the offline test launcher; exits non-zero unless network access is blocked.

Mode "os" runs under `python -I -S` (no sitecustomize) to prove the OS layer blocks egress.
Mode "python" runs with the test PYTHONPATH to prove tests/sitecustomize.py loaded at startup.
"""

import errno
import socket
import sys
from pathlib import Path

# RFC 5737 TEST-NET-1: never routed, so an unblocked attempt cannot reach a real service.
NON_ROUTABLE_PROBE = ("192.0.2.1", 9)
BLOCKED_ERRNOS = {errno.EPERM, errno.EACCES, errno.ENETUNREACH, errno.EHOSTUNREACH}


def check_os_isolation() -> None:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.settimeout(2)
    try:
        probe.connect(NON_ROUTABLE_PROBE)
    except OSError as error:
        if error.errno not in BLOCKED_ERRNOS:
            sys.exit(f"OS network isolation not confirmed: {type(error).__name__} errno={error.errno}")
    else:
        sys.exit("OS network isolation failed: outbound TCP connect succeeded.")
    finally:
        probe.close()
    # Only reached when TCP egress is already proven blocked.
    try:
        socket.getaddrinfo("offline-check.invalid", 443)
    except OSError:
        pass
    else:
        sys.exit("OS network isolation failed: DNS resolution succeeded.")
    print("OS-level network isolation verified (TCP and DNS blocked).")


def check_python_guard() -> None:
    expected = Path(__file__).resolve().parents[1] / "tests" / "sitecustomize.py"
    loaded = sys.modules.get("sitecustomize")
    if loaded is None or Path(loaded.__file__).resolve() != expected:
        sys.exit("tests/sitecustomize.py was not loaded at interpreter startup.")
    for name, attempt in (
        ("connect", lambda: socket.socket().connect(NON_ROUTABLE_PROBE)),
        ("create_connection", lambda: socket.create_connection(NON_ROUTABLE_PROBE, timeout=1)),
        ("getaddrinfo", lambda: socket.getaddrinfo("offline-check.invalid", 443)),
    ):
        try:
            attempt()
        except AssertionError:
            continue
        except OSError:
            sys.exit(f"Python network guard did not intercept socket.{name}.")
        sys.exit(f"Python network guard did not block socket.{name}.")
    print("Python startup network guard verified before test collection.")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "os":
        check_os_isolation()
    elif mode == "python":
        check_python_guard()
    else:
        sys.exit("usage: check_offline_isolation.py os|python")
