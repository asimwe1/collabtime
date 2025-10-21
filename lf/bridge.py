#!/usr/bin/env python3
"""
LF Tick Bridge

Minimal TCP client for receiving tick events from an external Lingua Franca program.
Falls back to an internal tick generator if needed for local testing.
"""

from __future__ import annotations

import socket
import time
from typing import Iterator, Optional


class LFTickClient:
    """Connects to an LF tick server and yields tick timestamps (ms)."""

    def __init__(self, host: str = "127.0.0.1", port: int = 9001, timeout_s: float = 5.0):
        self.host = host
        self.port = port
        self.timeout_s = timeout_s
        self.sock: Optional[socket.socket] = None

    def connect(self) -> None:
        """Connect to LF tick server. Raises exception on failure."""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self.timeout_s)
            sock.connect((self.host, self.port))
            # Optional: simple handshake
            sock.sendall(b"HELLO\n")
            self.sock = sock
        except Exception as e:
            print(f"Failed to connect to LF server at {self.host}:{self.port}: {e}")
            raise

    def ticks(self) -> Iterator[int]:
        if self.sock is None:
            raise RuntimeError("LFTickClient not connected")
        sock = self.sock
        buffer = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                line_s = line.decode("utf-8", errors="ignore").strip()
                if not line_s:
                    continue
                # Expected format: "TICK <ms>"
                parts = line_s.split()
                if parts and parts[0].upper() == "TICK":
                    try:
                        t_ms = int(parts[1]) if len(parts) > 1 else int(time.time() * 1000)
                    except Exception:
                        t_ms = int(time.time() * 1000)
                    yield t_ms


def internal_tick_generator(step_interval_ms: int) -> Iterator[int]:
    """Generate ticks locally at the given interval (ms)."""
    next_t = int(time.time() * 1000)
    while True:
        now_ms = int(time.time() * 1000)
        if now_ms >= next_t:
            yield now_ms
            next_t = now_ms + step_interval_ms
        else:
            time.sleep(max(0.0, (next_t - now_ms) / 1000.0))



