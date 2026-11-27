import logging
from typing import Any

log = logging.getLogger("search")

FIELDS = ("id", "category_id", "name", "price", "attributes", "description")


class FakeSearchIndex:
    def __init__(self) -> None:
        self.documents: dict[int, dict[str, Any]] = {}

    async def index(self, product: dict[str, Any]) -> None:
        self.documents[product["id"]] = {field: product[field] for field in FIELDS}
        log.info("indexed product %s", product["id"])

    async def delete(self, product_id: int) -> None:
        self.documents.pop(product_id, None)
        log.info("removed product %s", product_id)
