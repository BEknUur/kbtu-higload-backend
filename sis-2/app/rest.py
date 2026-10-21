from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Annotated

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse

from app import service
from app.db import engine
from app.schemas import CategoryOut, ProductOut, ProductPage


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await engine.dispose()  


app = FastAPI(title="Product catalog", lifespan=lifespan)


@app.exception_handler(service.NotFound)
async def not_found(request: Request, exc: service.NotFound):
    return JSONResponse(status_code=404, content={"detail": "product not found"})


@app.get("/api/v1/products/{product_id}")
async def get_product(product_id: int) -> ProductOut:
    return await service.get_product(product_id)


@app.get("/api/v1/products")
async def list_products(
    category_id: int | None = None,
    price_min: Annotated[Decimal | None, Query(ge=0)] = None,
    price_max: Annotated[Decimal | None, Query(ge=0)] = None,
    color: Annotated[str | None, Query(alias="attr.color")] = None,
    cursor: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ProductPage:
    return await service.list_products(
        cursor=cursor, limit=limit, category_id=category_id,
        price_min=price_min, price_max=price_max, color=color,
    )


@app.get("/api/v1/categories")
async def list_categories() -> list[CategoryOut]:
    return await service.list_categories()
