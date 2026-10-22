"""Fail closed on network access before pytest collects test modules."""

import socket


def _network_blocked(*args, **kwargs):
    raise AssertionError("Network access is disabled during pytest collection and tests.")


socket.socket.connect = _network_blocked
socket.socket.connect_ex = _network_blocked
socket.create_connection = _network_blocked
socket.getaddrinfo = _network_blocked