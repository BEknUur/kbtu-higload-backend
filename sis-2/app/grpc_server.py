import asyncio
import functools
from collections.abc import Awaitable, Callable
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import grpc
from google.protobuf import empty_pb2
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.exc import TimeoutError as PoolTimeout

from app import cache, service
from app import catalog_pb2 as pb
from app import catalog_pb2_grpc as pb_grpc
from app.config import settings
from app.db import engine
from app.schemas import ProductCreate, ProductOut, ProductUpdate

Handler = Callable[[Any, Any, grpc.aio.ServicerContext], Awaitable[Any]]


class InvalidArgument(Exception):
    pass


ERRORS: dict[type[Exception], grpc.StatusCode] = {
    service.NotFound: grpc.StatusCode.NOT_FOUND,
    service.VersionMismatch: grpc.StatusCode.FAILED_PRECONDITION,
    service.IdempotencyKeyReused: grpc.StatusCode.INVALID_ARGUMENT,
    InvalidArgument: grpc.StatusCode.INVALID_ARGUMENT,
    ValidationError: grpc.StatusCode.INVALID_ARGUMENT,
    InvalidOperation: grpc.StatusCode.INVALID_ARGUMENT,
    IntegrityError: grpc.StatusCode.ALREADY_EXISTS,
    service.Unavailable: grpc.StatusCode.UNAVAILABLE,
    PoolTimeout: grpc.StatusCode.UNAVAILABLE,
    OSError: grpc.StatusCode.UNAVAILABLE,
}


def metadata(context: grpc.aio.ServicerContext) -> dict[str, str]:
    return dict(context.invocation_metadata())


def handle_errors(method: Handler) -> Handler:
    @functools.wraps(method)
    async def wrapper(self: Any, request: Any, context: grpc.aio.ServicerContext) -> Any:
        deadline = asyncio.timeout(context.time_remaining())
        try:
            async with deadline:
                md = metadata(context)
                service.inject_fault(md.get("x-fault-id"), int(md.get("x-fail-times", 0)))
                return await method(self, request, context)
        except TimeoutError as exc:
            if deadline.expired():
                await context.abort(grpc.StatusCode.DEADLINE_EXCEEDED, "request deadline exceeded")
            await context.abort(grpc.StatusCode.UNAVAILABLE, type(exc).__name__)
        except tuple(ERRORS) as exc:
            code = next(code for error, code in ERRORS.items() if isinstance(exc, error))
            await context.abort(code, str(exc).split("\n")[0] or type(exc).__name__)

    return wrapper


def to_pb(product: ProductOut) -> pb.Product:
    message = pb.Product(
        id=product.id,
        sku=product.sku,
        name=product.name,
        description=product.description,
        price=str(product.price),
        category_id=product.category_id,
        attributes=product.attributes,
        image_keys=product.image_keys,
        version=product.version,
    )
    message.created_at.FromDatetime(product.created_at)
    message.updated_at.FromDatetime(product.updated_at)
    return message


def optional(request: Any, field: str) -> Any:
    return getattr(request, field) if request.HasField(field) else None


def optional_decimal(request: Any, field: str) -> Decimal | None:
    value = optional(request, field)
    return None if value is None else Decimal(value)


def idempotency_key(context: grpc.aio.ServicerContext) -> str:
    key = metadata(context).get("idempotency-key", "")
    if not 8 <= len(key) <= 128:
        raise InvalidArgument("idempotency-key metadata is required (8..128 chars)")
    return key


def mark_replayed(context: grpc.aio.ServicerContext, replayed: bool) -> None:
    if replayed:
        context.set_trailing_metadata((("idempotent-replayed", "true"),))


def update_changes(request: pb.UpdateProductRequest) -> dict[str, Any]:
    changes: dict[str, Any] = {}
    for path in request.update_mask.paths:
        if path not in ProductUpdate.model_fields:
            raise InvalidArgument(f"field {path!r} cannot be updated")
        value = getattr(request, path)
        changes[path] = dict(value) if path == "attributes" else list(value) if path == "image_keys" else value
    return changes


class Catalog(pb_grpc.ProductServiceServicer):
    @handle_errors
    async def GetProduct(self, request: pb.GetProductRequest, context: grpc.aio.ServicerContext) -> pb.Product:
        return to_pb(await service.get_product(request.id))

    @handle_errors
    async def ListProducts(
        self, request: pb.ListProductsRequest, context: grpc.aio.ServicerContext
    ) -> pb.ListProductsResponse:
        page = await service.list_products(
            cursor=request.cursor,
            limit=min(request.limit or 20, 100),
            category_id=optional(request, "category_id"),
            price_min=optional_decimal(request, "price_min"),
            price_max=optional_decimal(request, "price_max"),
            color=optional(request, "color"),
        )
        return pb.ListProductsResponse(items=[to_pb(p) for p in page.items], next_cursor=page.next_cursor)

    @handle_errors
    async def ListCategories(
        self, request: empty_pb2.Empty, context: grpc.aio.ServicerContext
    ) -> pb.ListCategoriesResponse:
        categories = await service.list_categories()
        return pb.ListCategoriesResponse(
            categories=[pb.Category(id=c.id, parent_id=c.parent_id, name=c.name) for c in categories]
        )

    @handle_errors
    async def CreateProduct(self, request: pb.CreateProductRequest, context: grpc.aio.ServicerContext) -> pb.Product:
        data = ProductCreate(
            sku=request.sku,
            name=request.name,
            description=request.description,
            price=request.price,
            category_id=request.category_id,
            attributes=dict(request.attributes),
            image_keys=list(request.image_keys),
        )
        product, replayed = await service.create_product(data, idempotency_key(context))
        mark_replayed(context, replayed)
        return to_pb(product)

    @handle_errors
    async def UpdateProduct(self, request: pb.UpdateProductRequest, context: grpc.aio.ServicerContext) -> pb.Product:
        data = ProductUpdate(**update_changes(request))
        product, replayed = await service.update_product(
            request.id, request.expected_version, data, idempotency_key(context)
        )
        mark_replayed(context, replayed)
        return to_pb(product)

    @handle_errors
    async def DeleteProduct(
        self, request: pb.DeleteProductRequest, context: grpc.aio.ServicerContext
    ) -> empty_pb2.Empty:
        await service.delete_product(request.id)
        return empty_pb2.Empty()


async def serve() -> None:
    server = grpc.aio.server()
    pb_grpc.add_ProductServiceServicer_to_server(Catalog(), server)
    credentials = grpc.ssl_server_credentials(
        [(Path(settings.tls_key).read_bytes(), Path(settings.tls_cert).read_bytes())]
    )
    server.add_secure_port(settings.grpc_bind, credentials)
    await server.start()
    try:
        await server.wait_for_termination()
    finally:
        await engine.dispose()
        await cache.client.aclose()


if __name__ == "__main__":
    asyncio.run(serve())
