"""Block network access from interpreter startup in offline test runs."""

import socket


def _network_blocked(*args, **kwargs):
    raise AssertionError("Network access is disabled in the offline test process.")


socket.socket.connect = _network_blocked
socket.socket.connect_ex = _network_blocked
socket.create_connection = _network_blocked
socket.getaddrinfo = _network_blocked