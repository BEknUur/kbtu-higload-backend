from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

Price = Annotated[
    Decimal,
    Field(ge=0, max_digits=12, decimal_places=2),
    AfterValidator(lambda v: v.quantize(Decimal("0.01"))),
]


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    sku: str
    name: str 
    description: str 
    price: Decimal
    category_id: int 
    attributes: dict 
    image_keys: list[str]
    version: int 
    created_at: datetime
    updated_at: datetime



class ProductPage(BaseModel):
    items: list[ProductOut]
    next_cursor: int | None


class ProductCreate(BaseModel):
    sku: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    price: Price
    category_id: int
    attributes: dict = {}
    image_keys: list[str] = []


class ProductUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = None
    price: Price | None = None
    category_id: int | None = None
    attributes: dict | None = None
    image_keys: list[str] | None = None

    @model_validator(mode="after")
    def no_nulls(self):
        if any(v is None for v in self.model_dump(exclude_unset=True).values()):
            raise ValueError("fields cannot be null")
        return self


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    parent_id: int | None
    name: str