from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Annotated

from fastapi import FastAPI, Header, Query, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from app import service
from app.db import engine
from app.schemas import CategoryOut, ProductCreate, ProductOut, ProductPage, ProductUpdate

IdempotencyKey = Annotated[str, Header(min_length=8, max_length=128)]  # header "Idempotency-Key"


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await engine.dispose()  


app = FastAPI(title="Product catalog", lifespan=lifespan)


@app.exception_handler(service.NotFound)
async def not_found(request: Request, exc: service.NotFound):
    return JSONResponse(status_code=404, content={"detail": "product not found"})


@app.exception_handler(service.VersionMismatch)
async def version_mismatch(request: Request, exc: service.VersionMismatch):
    return JSONResponse(status_code=412, content={"detail": "version is stale, re-read the product"})


@app.exception_handler(service.IdempotencyKeyReused)
async def key_reused(request: Request, exc: service.IdempotencyKeyReused):
    return JSONResponse(status_code=422, content={"detail": "Idempotency-Key was used for a different request"})


@app.exception_handler(IntegrityError)
async def integrity_error(request: Request, exc: IntegrityError):
    return JSONResponse(status_code=409, content={"detail": str(exc.orig).split("\n")[0]})


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


@app.post("/api/v1/admin/products", status_code=201)
async def create_product(
    body: ProductCreate, 
    idempotency_key: IdempotencyKey, 
    response: Response) -> ProductOut:
    product, replayed = await service.create_product(body, idempotency_key)
    if replayed:
        response.headers["Idempotent-Replayed"] = "true"
    return product


@app.patch("/api/v1/admin/products/{product_id}")
async def update_product(
    product_id: int, 
    body: ProductUpdate, 
    idempotency_key: IdempotencyKey,
    if_match: Annotated[int, Header()], 
    response: Response) -> ProductOut:

    product, replayed = await service.update_product(product_id, if_match, body, idempotency_key)
    if replayed:
        response.headers["Idempotent-Replayed"] = "true"
    return product


@app.delete("/api/v1/admin/products/{product_id}", status_code=204)
async def delete_product(product_id: int) -> None:
    await service.delete_product(product_id)
