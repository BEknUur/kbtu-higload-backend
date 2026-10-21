import hashlib
import json
from collections import Counter
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app import cache
from app.config import settings
from app.db import Session
from app.models import Category, IdempotencyKey, Product
from app.schemas import ProductCreate, ProductOut, ProductPage, ProductUpdate

ACTIVE = "active"
DELETED = "deleted"

WriteResult = tuple[ProductOut, bool]


class NotFound(Exception):
    pass


class VersionMismatch(Exception):
    pass


class IdempotencyKeyReused(Exception):
    pass


class Unavailable(Exception):
    pass


_fault_attempts: Counter[str] = Counter()


def inject_fault(fault_id: str | None, fail_times: int) -> None:
    if not settings.fault_injection or not fault_id:
        return
    _fault_attempts[fault_id] += 1
    if _fault_attempts[fault_id] <= fail_times:
        raise Unavailable(f"injected fault {_fault_attempts[fault_id]}/{fail_times}")


async def get_product(product_id: int) -> ProductOut:
    cached = await cache.get(product_id)
    if cached == cache.MISSING:
        raise NotFound
    if cached is not None:
        return ProductOut.model_validate_json(cached)

    async with Session() as session:
        product = await session.get(Product, product_id)
    if product is None or product.status != ACTIVE:
        await cache.put_missing(product_id)
        raise NotFound

    result = ProductOut.model_validate(product)
    await cache.put(product_id, result.model_dump_json())
    return result


async def list_products(
    cursor: int = 0,
    limit: int = 20,
    category_id: int | None = None,
    price_min: Decimal | None = None,
    price_max: Decimal | None = None,
    color: str | None = None,
) -> ProductPage:
    query = select(Product).where(Product.status == ACTIVE, Product.id > cursor)
    if category_id is not None:
        query = query.where(Product.category_id == category_id)
    if price_min is not None:
        query = query.where(Product.price >= price_min)
    if price_max is not None:
        query = query.where(Product.price <= price_max)
    if color is not None:
        query = query.where(Product.attributes.contains({"color": color}))
    query = query.order_by(Product.id).limit(limit)

    async with Session() as session:
        items = (await session.scalars(query)).all()
    next_cursor = items[-1].id if len(items) == limit else None
    return ProductPage(items=items, next_cursor=next_cursor)


async def list_categories() -> Sequence[Category]:
    async with Session() as session:
        return (await session.scalars(select(Category).order_by(Category.id))).all()


async def create_product(data: ProductCreate, key: str) -> WriteResult:
    request_hash = _request_hash("create", None, None, data.model_dump(mode="json"))

    async with Session() as session, session.begin():
        stored = await _claim_key(session, key, request_hash)
        if stored:
            return ProductOut.model_validate(stored.response), True

        product = Product(**data.model_dump())
        session.add(product)
        await session.flush()

        result = ProductOut.model_validate(product)
        await _save_result(session, key, result)
    await cache.delete(result.id)
    return result, False


async def update_product(product_id: int, expected_version: int, data: ProductUpdate, key: str) -> WriteResult:
    changes = data.model_dump(exclude_unset=True)
    request_hash = _request_hash(
        "update", product_id, expected_version, data.model_dump(mode="json", exclude_unset=True)
    )

    async with Session() as session, session.begin():
        stored = await _claim_key(session, key, request_hash)
        if stored:
            return ProductOut.model_validate(stored.response), True

        product = await session.scalar(
            update(Product)
            .where(Product.id == product_id, Product.status == ACTIVE, Product.version == expected_version)
            .values(**changes, version=Product.version + 1, updated_at=func.now())
            .returning(Product)
        )
        if product is None:
            raise VersionMismatch if await _is_active(session, product_id) else NotFound

        result = ProductOut.model_validate(product)
        await _save_result(session, key, result)
    await cache.delete(product_id)
    return result, False


async def delete_product(product_id: int) -> None:
    async with Session() as session, session.begin():
        await session.execute(
            update(Product)
            .where(Product.id == product_id, Product.status == ACTIVE)
            .values(status=DELETED, version=Product.version + 1, updated_at=func.now())
        )
    await cache.delete(product_id)


async def _is_active(session: AsyncSession, product_id: int) -> bool:
    found = await session.scalar(select(Product.id).where(Product.id == product_id, Product.status == ACTIVE))
    return found is not None


def _request_hash(operation: str, product_id: int | None, version: int | None, data: dict[str, Any]) -> str:
    raw = json.dumps([operation, product_id, version, data], sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


async def _claim_key(session: AsyncSession, key: str, request_hash: str) -> IdempotencyKey | None:
    inserted = await session.scalar(
        insert(IdempotencyKey)
        .values(key=key, request_hash=request_hash)
        .on_conflict_do_nothing()
        .returning(IdempotencyKey.key)
    )
    if inserted:
        return None

    stored = await session.get(IdempotencyKey, key)
    if stored is None or stored.request_hash != request_hash:
        raise IdempotencyKeyReused
    return stored


async def _save_result(session: AsyncSession, key: str, result: ProductOut) -> None:
    await session.execute(
        update(IdempotencyKey)
        .where(IdempotencyKey.key == key)
        .values(response=result.model_dump(mode="json"))
    )
