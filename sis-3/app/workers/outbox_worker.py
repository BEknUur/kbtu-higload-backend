import asyncio
import logging

from sqlalchemy import func, select

from app.cache import cache
from app.config import settings
from app.database.db import Session, dispose
from app.database.models import OutboxEvent
from app.services.search import FakeSearchIndex

BATCH_SIZE = 100
DELETED = "product.deleted"


async def process_batch(search: FakeSearchIndex) -> int:
    async with Session() as session, session.begin():
        events = (
            await session.scalars(
                select(OutboxEvent)
                .where(OutboxEvent.processed_at.is_(None))
                .order_by(OutboxEvent.id)
                .limit(BATCH_SIZE)
                .with_for_update(skip_locked=True)
            )
        ).all()
        for event in events:
            await cache.delete_or_raise(event.product_id)
            if event.event_type == DELETED:
                await search.delete(event.product_id)
            else:
                await search.index(event.payload)
            event.processed_at = func.now()
    return len(events)


async def run() -> None:
    search = FakeSearchIndex()
    try:
        while True:
            try:
                processed = await process_batch(search)
            except Exception:
                logging.exception("outbox batch failed, retrying")
                processed = 0
            if not processed:
                await asyncio.sleep(settings.outbox_poll_interval)
    finally:
        await dispose()
        await cache.client.aclose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    asyncio.run(run())
