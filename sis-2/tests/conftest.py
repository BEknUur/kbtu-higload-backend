from collections.abc import AsyncIterator, Iterator

import httpx
import pytest

from app import catalog_pb2_grpc as pb_grpc
from app.clients import grpc_stub, rest_client
from devservers import running_servers


@pytest.fixture(scope="session", autouse=True)
def servers() -> Iterator[None]:
    with running_servers(fault_injection="true"):
        yield


@pytest.fixture
async def rest() -> AsyncIterator[httpx.AsyncClient]:
    async with rest_client(http2=True) as client:
        yield client


@pytest.fixture
async def grpc_api() -> AsyncIterator[pb_grpc.ProductServiceStub]:
    channel, stub = grpc_stub()
    async with channel:
        yield stub
