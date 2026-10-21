from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


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


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    parent_id: int | None
    name: str