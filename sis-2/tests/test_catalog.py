import asyncio
import uuid

import grpc
import httpx
import pytest
import redis.asyncio as redis
from google.protobuf import field_mask_pb2

from app import cache, service
from app import catalog_pb2 as pb
from app import catalog_pb2_grpc as pb_grpc
from app.clients import rest_request
from app.config import settings
from app.db import engine

PRODUCTS = "/api/v1/admin/products"


def unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def new_product() -> dict:
    return {"sku": unique("SKU"), "name": "Test", "price": "9.99", "category_id": 12, "attributes": {"color": "red"}}


async def create(rest: httpx.AsyncClient, body: dict | None = None, key: str | None = None) -> httpx.Response:
    return await rest.post(PRODUCTS, json=body or new_product(), headers={"Idempotency-Key": key or unique("key")})


async def count_by_sku(rest: httpx.AsyncClient, sku: str) -> int:
    found, cursor = 0, None
    while True:
        params = {"category_id": 12, "limit": 100, **({"cursor": cursor} if cursor else {})}
        page = (await rest.get("/api/v1/products", params=params)).json()
        found += sum(item["sku"] == sku for item in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            return found


async def test_http2_is_negotiated(rest: httpx.AsyncClient) -> None:
    response = await rest.get("/api/v1/products/1")
    assert response.http_version == "HTTP/2"


async def test_rest_and_grpc_return_the_same_product(
    rest: httpx.AsyncClient, grpc_api: pb_grpc.ProductServiceStub
) -> None:
    from_rest = (await rest.get("/api/v1/products/1")).json()
    from_grpc = await grpc_api.GetProduct(pb.GetProductRequest(id=1))

    assert from_grpc.price == from_rest["price"]
    assert (from_grpc.id, from_grpc.sku, from_grpc.name, from_grpc.version) == (
        from_rest["id"], from_rest["sku"], from_rest["name"], from_rest["version"]
    )
    assert dict(from_grpc.attributes) == from_rest["attributes"]
    assert list(from_grpc.image_keys) == from_rest["image_keys"]


async def test_cursor_pages_have_no_gaps_or_duplicates(rest: httpx.AsyncClient) -> None:
    async def all_ids(limit: int) -> list[int]:
        ids, cursor = [], 0
        while True:
            page = (await rest.get("/api/v1/products", params={"category_id": 12, "limit": limit, "cursor": cursor})).json()
            ids += [item["id"] for item in page["items"]]
            if page["next_cursor"] is None:
                return ids
            cursor = page["next_cursor"]

    small_pages = await all_ids(7)
    assert small_pages == sorted(set(small_pages))
    assert small_pages == await all_ids(100)


async def test_deleted_product_is_hidden(rest: httpx.AsyncClient) -> None:
    product = (await create(rest)).json()

    assert (await rest.delete(f"{PRODUCTS}/{product['id']}")).status_code == 204
    assert (await rest.get(f"/api/v1/products/{product['id']}")).status_code == 404
    assert await count_by_sku(rest, product["sku"]) == 0


async def test_repeated_delete_changes_nothing(rest: httpx.AsyncClient) -> None:
    product = (await create(rest)).json()
    await rest.delete(f"{PRODUCTS}/{product['id']}")

    async with engine.connect() as conn:
        before = (await conn.exec_driver_sql(f"SELECT version, updated_at FROM products WHERE id = {product['id']}")).one()
        assert (await rest.delete(f"{PRODUCTS}/{product['id']}")).status_code == 204
        after = (await conn.exec_driver_sql(f"SELECT version, updated_at FROM products WHERE id = {product['id']}")).one()
    await engine.dispose()
    assert before == after


async def test_two_updates_with_same_version_only_one_wins(rest: httpx.AsyncClient) -> None:
    product = (await create(rest)).json()

    async def patch(name: str) -> int:
        response = await rest.patch(
            f"{PRODUCTS}/{product['id']}",
            json={"name": name},
            headers={"Idempotency-Key": unique("key"), "If-Match": "1"},
        )
        return response.status_code

    assert sorted(await asyncio.gather(patch("A"), patch("B"))) == [200, 412]


async def test_update_of_missing_product_is_404_not_412(rest: httpx.AsyncClient) -> None:
    response = await rest.patch(
        f"{PRODUCTS}/999999999", json={"name": "X"}, headers={"Idempotency-Key": unique("key"), "If-Match": "1"}
    )
    assert response.status_code == 404


async def test_concurrent_duplicates_create_one_product(rest: httpx.AsyncClient) -> None:
    body, key = new_product(), unique("key")

    responses = await asyncio.gather(*(create(rest, body, key) for _ in range(10)))

    assert {r.status_code for r in responses} == {201}
    assert len({r.json()["id"] for r in responses}) == 1
    assert await count_by_sku(rest, body["sku"]) == 1


async def test_retry_through_other_protocol_replays_result(
    rest: httpx.AsyncClient, grpc_api: pb_grpc.ProductServiceStub
) -> None:
    body, key = new_product(), unique("key")
    first = (await create(rest, body, key)).json()

    call = grpc_api.CreateProduct(
        pb.CreateProductRequest(
            sku=body["sku"], name=body["name"], price="9.990", category_id=12, attributes=body["attributes"]
        ),
        metadata=(("idempotency-key", key),),
    )
    replay = await call

    assert replay.id == first["id"]
    assert ("idempotent-replayed", "true") in tuple(await call.trailing_metadata())


async def test_update_retry_does_not_bump_version_twice(rest: httpx.AsyncClient) -> None:
    product = (await create(rest)).json()
    headers = {"Idempotency-Key": unique("key"), "If-Match": "1"}

    first = await rest.patch(f"{PRODUCTS}/{product['id']}", json={"price": "1.00"}, headers=headers)
    second = await rest.patch(f"{PRODUCTS}/{product['id']}", json={"price": "1.00"}, headers=headers)

    assert first.json()["version"] == second.json()["version"] == 2
    assert second.headers["idempotent-replayed"] == "true"


async def test_key_reused_for_other_request_is_rejected(
    rest: httpx.AsyncClient, grpc_api: pb_grpc.ProductServiceStub
) -> None:
    key = unique("key")
    await create(rest, key=key)

    assert (await create(rest, key=key)).status_code == 422
    with pytest.raises(grpc.aio.AioRpcError) as error:
        await grpc_api.CreateProduct(
            pb.CreateProductRequest(sku=unique("SKU"), name="Other", price="1", category_id=12),
            metadata=(("idempotency-key", key),),
        )
    assert error.value.code() == grpc.StatusCode.INVALID_ARGUMENT


async def test_lost_response_then_retry_does_not_duplicate(rest: httpx.AsyncClient) -> None:
    body, key = new_product(), unique("key")
    lost = await create(rest, body, key)

    retry = await create(rest, body, key)

    assert retry.json()["id"] == lost.json()["id"]
    assert await count_by_sku(rest, body["sku"]) == 1


async def test_failed_write_rolls_back_idempotency_key(rest: httpx.AsyncClient) -> None:
    key = unique("key")
    taken_sku = (await create(rest)).json()["sku"]

    duplicate = await create(rest, {**new_product(), "sku": taken_sku}, key)
    assert duplicate.status_code == 409

    fixed = await create(rest, new_product(), key)
    assert fixed.status_code == 201


async def test_patch_null_and_immutable_fields_are_rejected(
    rest: httpx.AsyncClient, grpc_api: pb_grpc.ProductServiceStub
) -> None:
    product = (await create(rest)).json()
    headers = {"Idempotency-Key": unique("key"), "If-Match": "1"}
    assert (await rest.patch(f"{PRODUCTS}/{product['id']}", json={"name": None}, headers=headers)).status_code == 422

    with pytest.raises(grpc.aio.AioRpcError) as error:
        await grpc_api.UpdateProduct(
            pb.UpdateProductRequest(
                id=product["id"], expected_version=1, update_mask=field_mask_pb2.FieldMask(paths=["sku"])
            ),
            metadata=(("idempotency-key", unique("key")),),
        )
    assert error.value.code() == grpc.StatusCode.INVALID_ARGUMENT


async def test_rest_retries_two_faults_then_succeeds(rest: httpx.AsyncClient) -> None:
    fault = {"x-fault-id": unique("fault"), "x-fail-times": "2"}
    response = await rest_request(rest, "GET", "/api/v1/products/2", headers=fault)
    assert response.status_code == 200


async def test_rest_gives_up_after_three_attempts(rest: httpx.AsyncClient) -> None:
    fault_id = unique("fault")
    response = await rest_request(rest, "GET", "/api/v1/products/2", headers={"x-fault-id": fault_id, "x-fail-times": "5"})
    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"

    fourth = await rest.get("/api/v1/products/2", headers={"x-fault-id": fault_id, "x-fail-times": "3"})
    assert fourth.status_code == 200


async def test_rest_does_not_retry_client_errors(rest: httpx.AsyncClient) -> None:
    fault_id = unique("fault")
    response = await rest_request(rest, "GET", "/api/v1/products/999999999", headers={"x-fault-id": fault_id})
    assert response.status_code == 404


async def test_rest_write_retry_keeps_the_same_key(rest: httpx.AsyncClient) -> None:
    body = new_product()
    headers = {"Idempotency-Key": unique("key"), "x-fault-id": unique("fault"), "x-fail-times": "2"}
    response = await rest_request(rest, "POST", PRODUCTS, json=body, headers=headers)
    assert response.status_code == 201
    assert await count_by_sku(rest, body["sku"]) == 1


async def test_grpc_retries_two_faults_then_succeeds(grpc_api: pb_grpc.ProductServiceStub) -> None:
    metadata = (("x-fault-id", unique("fault")), ("x-fail-times", "2"))
    product = await grpc_api.GetProduct(pb.GetProductRequest(id=2), metadata=metadata, timeout=3)
    assert product.id == 2


async def test_grpc_gives_up_after_three_attempts(grpc_api: pb_grpc.ProductServiceStub) -> None:
    fault_id = unique("fault")
    with pytest.raises(grpc.aio.AioRpcError) as error:
        await grpc_api.GetProduct(
            pb.GetProductRequest(id=2), metadata=(("x-fault-id", fault_id), ("x-fail-times", "5")), timeout=3
        )
    assert error.value.code() == grpc.StatusCode.UNAVAILABLE

    fourth = await grpc_api.GetProduct(
        pb.GetProductRequest(id=2), metadata=(("x-fault-id", fault_id), ("x-fail-times", "3")), timeout=3
    )
    assert fourth.id == 2


async def test_rest_server_deadline_returns_504(rest: httpx.AsyncClient) -> None:
    response = await rest.get("/api/v1/products", params={"limit": 100}, headers={"X-Request-Timeout": "1"})
    assert response.status_code == 504


async def test_rest_client_budget_limits_all_attempts(rest: httpx.AsyncClient) -> None:
    with pytest.raises(httpx.TimeoutException):
        await rest_request(rest, "GET", "/api/v1/products", params={"limit": 100}, budget=0.001)


async def test_grpc_deadline_is_exceeded(grpc_api: pb_grpc.ProductServiceStub) -> None:
    with pytest.raises(grpc.aio.AioRpcError) as error:
        await grpc_api.ListProducts(pb.ListProductsRequest(limit=100), timeout=0.001)
    assert error.value.code() == grpc.StatusCode.DEADLINE_EXCEEDED


async def test_cache_hit_and_invalidation(rest: httpx.AsyncClient) -> None:
    product = (await create(rest)).json()
    key = f"product:{product['id']}"
    redis_client = redis.from_url(settings.redis_url, decode_responses=True)

    await rest.get(f"/api/v1/products/{product['id']}")
    assert await redis_client.exists(key)

    await rest.patch(
        f"{PRODUCTS}/{product['id']}", json={"name": "Fresh"}, headers={"Idempotency-Key": unique("key"), "If-Match": "1"}
    )
    assert not await redis_client.exists(key)
    assert (await rest.get(f"/api/v1/products/{product['id']}")).json()["name"] == "Fresh"
    await redis_client.aclose()


async def test_missing_product_is_cached_as_404(rest: httpx.AsyncClient) -> None:
    redis_client = redis.from_url(settings.redis_url, decode_responses=True)
    await rest.get("/api/v1/products/987654321")
    assert await redis_client.get("product:987654321") == cache.MISSING
    await redis_client.aclose()


async def test_catalog_works_when_redis_is_down(monkeypatch: pytest.MonkeyPatch) -> None:
    dead = redis.from_url("redis://127.0.0.1:1", socket_timeout=0.01, socket_connect_timeout=0.01)
    monkeypatch.setattr(cache, "client", dead)

    product = await service.get_product(1)

    assert product.id == 1
    await engine.dispose()
