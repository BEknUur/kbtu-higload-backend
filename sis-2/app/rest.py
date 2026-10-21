from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, FastAPI, Header, Query, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from app import service
from app.db import engine
from app.schemas import CategoryOut, ProductCreate, ProductOut, ProductPage, ProductUpdate

IdempotencyKey = Annotated[str, Header(min_length=8, max_length=128)]
IfMatch = Annotated[int, Header()]


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await engine.dispose()


app = FastAPI(title="Product catalog", lifespan=lifespan)
router = APIRouter(prefix="/api/v1")


def error(status_code: int, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": detail})


@app.exception_handler(service.NotFound)
async def not_found(request: Request, exc: service.NotFound) -> JSONResponse:
    return error(404, "product not found")


@app.exception_handler(service.VersionMismatch)
async def version_mismatch(request: Request, exc: service.VersionMismatch) -> JSONResponse:
    return error(412, "version is stale, re-read the product")


@app.exception_handler(service.IdempotencyKeyReused)
async def key_reused(request: Request, exc: service.IdempotencyKeyReused) -> JSONResponse:
    return error(422, "Idempotency-Key was used for a different request")


@app.exception_handler(IntegrityError)
async def integrity_error(request: Request, exc: IntegrityError) -> JSONResponse:
    return error(409, str(exc.orig).split("\n")[0])


def mark_replayed(response: Response, replayed: bool) -> None:
    if replayed:
        response.headers["Idempotent-Replayed"] = "true"


@router.get("/products/{product_id}")
async def get_product(product_id: int) -> ProductOut:
    return await service.get_product(product_id)


@router.get("/products")
async def list_products(
    category_id: int | None = None,
    price_min: Annotated[Decimal | None, Query(ge=0)] = None,
    price_max: Annotated[Decimal | None, Query(ge=0)] = None,
    color: Annotated[str | None, Query(alias="attr.color")] = None,
    cursor: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ProductPage:
    return await service.list_products(cursor, limit, category_id, price_min, price_max, color)


@router.get("/categories")
async def list_categories() -> list[CategoryOut]:
    return await service.list_categories()


@router.post("/admin/products", status_code=201)
async def create_product(body: ProductCreate, idempotency_key: IdempotencyKey, response: Response) -> ProductOut:
    product, replayed = await service.create_product(body, idempotency_key)
    mark_replayed(response, replayed)
    return product


@router.patch("/admin/products/{product_id}")
async def update_product(
    product_id: int,
    body: ProductUpdate,
    idempotency_key: IdempotencyKey,
    if_match: IfMatch,
    response: Response,
) -> ProductOut:
    product, replayed = await service.update_product(product_id, if_match, body, idempotency_key)
    mark_replayed(response, replayed)
    return product


@router.delete("/admin/products/{product_id}", status_code=204)
async def delete_product(product_id: int) -> None:
    await service.delete_product(product_id)


app.include_router(router)
