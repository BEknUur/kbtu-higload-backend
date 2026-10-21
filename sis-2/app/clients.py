import asyncio
import json
import random
import ssl
import time
from pathlib import Path
from typing import Any

import grpc
import httpx

from app import catalog_pb2_grpc as pb_grpc
from app.config import settings

ATTEMPTS = 3
ATTEMPT_TIMEOUT = 1.0
BUDGET = 3.0
RETRYABLE_STATUS = {502, 503, 504}


def backoff(attempt: int) -> float:
    return 0.1 * 2**attempt + random.uniform(0, 0.05)


def rest_client(http2: bool) -> httpx.AsyncClient:
    tls = ssl.create_default_context(cafile=settings.tls_cert)
    return httpx.AsyncClient(
        base_url=settings.rest_url, http1=not http2, http2=http2, verify=tls, timeout=ATTEMPT_TIMEOUT
    )


async def rest_request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    attempts: int = ATTEMPTS,
    budget: float = BUDGET,
    **kwargs: Any,
) -> httpx.Response:
    deadline = time.monotonic() + budget
    response: httpx.Response | None = None
    error: httpx.TransportError | None = None

    for attempt in range(attempts):
        timeout = min(ATTEMPT_TIMEOUT, deadline - time.monotonic())
        attempt_headers = {**(headers or {}), "X-Request-Timeout": f"{timeout * 1000:.0f}"}
        try:
            response, error = await client.request(method, url, headers=attempt_headers, timeout=timeout, **kwargs), None
            if response.status_code not in RETRYABLE_STATUS:
                return response
        except httpx.TransportError as exc:
            response, error = None, exc

        pause = backoff(attempt)
        if attempt == attempts - 1 or time.monotonic() + pause >= deadline:
            break
        await asyncio.sleep(pause)

    if error is not None:
        raise error
    assert response is not None
    return response


def grpc_service_config(retries: bool) -> str:
    method_config: dict[str, Any] = {"name": [{"service": "catalog.v1.ProductService"}]}
    if retries:
        method_config["retryPolicy"] = {
            "maxAttempts": ATTEMPTS,
            "initialBackoff": "0.1s",
            "maxBackoff": "0.2s",
            "backoffMultiplier": 2,
            "retryableStatusCodes": ["UNAVAILABLE"],
        }
    return json.dumps({"methodConfig": [method_config]})


def grpc_stub(retries: bool = True) -> tuple[grpc.aio.Channel, pb_grpc.ProductServiceStub]:
    credentials = grpc.ssl_channel_credentials(Path(settings.tls_cert).read_bytes())
    channel = grpc.aio.secure_channel(
        settings.grpc_target,
        credentials,
        options=[
            ("grpc.service_config", grpc_service_config(retries)),
            ("grpc.enable_retries", int(retries)),
        ],
    )
    return channel, pb_grpc.ProductServiceStub(channel)
