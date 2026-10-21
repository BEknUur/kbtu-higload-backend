import contextlib
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator

from app.config import settings

REST_PORT = 8443
GRPC_PORT = int(settings.grpc_bind.rsplit(":", 1)[1])

REST = [sys.executable, "-m", "hypercorn", "--config", "hypercorn.toml", "app.rest:app"]
GRPC = [sys.executable, "-m", "app.grpc_server"]


def port_open(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def wait_for_port(port: int, timeout: float = 15) -> None:
    deadline = time.monotonic() + timeout
    while not port_open(port):
        if time.monotonic() > deadline:
            raise RuntimeError(f"port {port} did not open")
        time.sleep(0.1)


@contextlib.contextmanager
def running_servers(**env: str) -> Iterator[None]:
    busy = [port for port in (REST_PORT, GRPC_PORT) if port_open(port)]
    if busy:
        raise RuntimeError(f"ports {busy} are busy: stop `make rest-tls` / `make grpc` first")

    server_env = {**os.environ, **{name.upper(): value for name, value in env.items()}}
    processes = [subprocess.Popen(command, env=server_env) for command in (REST, GRPC)]
    try:
        wait_for_port(REST_PORT)
        wait_for_port(GRPC_PORT)
        yield
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            process.wait(timeout=10)
