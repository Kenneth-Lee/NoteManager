"""IPC protocol for NoteManager daemon.

Protocol: 4-byte big-endian length header + JSON body.
"""

import json
import os
import socket
import struct
from pathlib import Path

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 19832
DEFAULT_UNIX_SOCKET = Path.home() / ".NoteManager" / "daemon.sock"


def use_unix_socket() -> bool:
    """Use Unix domain socket on non-Windows systems."""
    return os.name != "nt"


def send_command(cmd: dict, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
                 timeout: float = 30.0, socket_path: str | None = None) -> dict:
    """Send a command to the daemon and return the response.

    Args:
        cmd: Command dict, e.g. {"cmd": "add", "directory": "/path"}
        host: Daemon host.
        port: Daemon port.
        timeout: Socket timeout in seconds.

    Returns:
        Response dict from daemon.
    """
    body = json.dumps(cmd, ensure_ascii=False).encode("utf-8")
    header = struct.pack(">I", len(body))

    if use_unix_socket():
        target = socket_path or str(DEFAULT_UNIX_SOCKET)
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(timeout)
                sock.connect(target)
                sock.sendall(header + body)
                return recv_response(sock)
        except OSError:
            # Backward compatibility: old daemon may still use TCP.
            pass

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        sock.connect((host, port))
        sock.sendall(header + body)
        return recv_response(sock)


def recv_response(sock: socket.socket) -> dict:
    """Receive a response from the daemon.

    Args:
        sock: Connected socket.

    Returns:
        Response dict.
    """
    header = _recv_exact(sock, 4)
    length = struct.unpack(">I", header)[0]
    body = _recv_exact(sock, length)
    return json.loads(body.decode("utf-8"))


def send_response(sock: socket.socket, resp: dict) -> None:
    """Send a response back to the client.

    Args:
        sock: Connected socket.
        resp: Response dict.
    """
    body = json.dumps(resp, ensure_ascii=False).encode("utf-8")
    header = struct.pack(">I", len(body))
    sock.sendall(header + body)


def recv_command(sock: socket.socket) -> dict:
    """Receive a command from the client.

    Args:
        sock: Connected socket.

    Returns:
        Command dict.
    """
    header = _recv_exact(sock, 4)
    length = struct.unpack(">I", header)[0]
    body = _recv_exact(sock, length)
    return json.loads(body.decode("utf-8"))


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    """Receive exactly n bytes from socket."""
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise ConnectionError("Connection closed")
        data += chunk
    return data
