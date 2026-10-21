from decimal import Decimal

from sqlalchemy import select

from app.db import Session
from app.models import Category, Product


class NotFound(Exception):
    pass


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
