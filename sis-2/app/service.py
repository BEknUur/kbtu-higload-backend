import hashlib
import json
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert

from app.db import Session
from app.models import Category, IdempotencyKey, Product
from app.schemas import ProductCreate, ProductOut, ProductUpdate


class NotFound(Exception):
    pass


class VersionMismatch(Exception):
    pass


class IdempotencyKeyReused(Exception):
    """Same Idempotency-Key, different request."""


async def get_product(product_id: int) -> Product:
    async with Session() as s:
        p = await s.get(Product, product_id)
    if p is None or p.status != "active":
        raise NotFound
    return p


async def list_products(
        cursor: int = 0,
        limit: int = 20,
        category_id: int | None = None,
        price_min: Decimal | None = None,
        price_max: Decimal | None = None,
        color: str | None = None,
        ):
    q = select(Product).where(Product.status == "active", Product.id > cursor)
    if category_id is not None:
        q = q.where(Product.category_id == category_id)
    if price_min is not None:
        q = q.where(Product.price >= price_min)
    if price_max is not None:
        q = q.where(Product.price <= price_max)
    if color is not None:
        q = q.where(Product.attributes.contains({"color": color}))

    async with Session() as s:
        items = (await s.scalars(q.order_by(Product.id).limit(limit))).all()
    next_cursor = items[-1].id if len(items) == limit else None
    return {"items": items, "next_cursor": next_cursor}


async def list_categories():
    async with Session() as s:
        return (await s.scalars(select(Category).order_by(Category.id))).all()


def _request_hash(
        operation: str, 
        product_id: int | None, 
        version: int | None, 
        data: dict) -> str:
    raw = json.dumps([operation, product_id, version, data], sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


async def _claim_key(s, 
                     key: str, 
                     request_hash: str) -> IdempotencyKey | None:
    inserted = await s.scalar(
        insert(IdempotencyKey)
        .values(key=key, request_hash=request_hash)
        .on_conflict_do_nothing()  
        .returning(IdempotencyKey.key)
    )
    if inserted:
        return None
    stored = await s.get(IdempotencyKey, key)
    if stored.request_hash != request_hash:
        raise IdempotencyKeyReused
    return stored


async def _save_result(s, key: str, out: ProductOut) -> None:
    await s.execute(
        update(IdempotencyKey).where(IdempotencyKey.key == key).values(response=out.model_dump(mode="json"))
    )


async def create_product(data: ProductCreate, key: str) -> tuple[ProductOut, bool]:

    h = _request_hash("create", None, None, data.model_dump(mode="json"))
    async with Session() as s, s.begin():
        stored = await _claim_key(s, key, h)
        if stored:
            return ProductOut.model_validate(stored.response), True
        p = Product(**data.model_dump())
        s.add(p)
        await s.flush()  
        out = ProductOut.model_validate(p)
        await _save_result(s, key, out)
    return out, False


async def update_product(
        product_id: int, 
        expected_version: int, 
        data: ProductUpdate,
        key: str
        ) -> tuple[ProductOut, bool]:
    changes = data.model_dump(exclude_unset=True)
    h = _request_hash("update", product_id, expected_version, data.model_dump(mode="json", exclude_unset=True))
    async with Session() as s, s.begin():
        stored = await _claim_key(s, key, h)
        if stored:
            return ProductOut.model_validate(stored.response), True
        p = await s.scalar(
            update(Product)
            .where(Product.id == product_id, Product.status == "active", Product.version == expected_version)
            .values(**changes, version=Product.version + 1, updated_at=func.now())
            .returning(Product)
        )
        if p is None:
            exists = await s.scalar(
                select(Product.id).where(Product.id == product_id, Product.status == "active")
            )
            raise VersionMismatch if exists else NotFound
        out = ProductOut.model_validate(p)
        await _save_result(s, key, out)
    return out, False


async def delete_product(product_id: int) -> None:
    async with Session() as s, s.begin():
        await s.execute(
            update(Product)
            .where(Product.id == product_id, Product.status == "active")
            .values(status="deleted", version=Product.version + 1, updated_at=func.now())
        )
