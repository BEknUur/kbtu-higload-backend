import asyncio
import os
import random
import statistics
import sys
import time
from collections import defaultdict

import asyncpg
import uvloop

PRODUCTS = 1_000_000
STAGE = os.environ.get("STAGE", "test")
RATE = int(os.environ.get("RATE", 500))
SECONDS = 10 if STAGE == "warmup" else 30
POOL = int(os.environ.get("POOL", 80))
WRITE_RATE = 20
BACKENDS = "SELECT count(*) FROM pg_stat_activity WHERE backend_type = 'client backend'"
TIMEOUT = 1.0

GET = "SELECT * FROM products WHERE id = $1"
LIST_CATEGORY = """
    SELECT id, name, price FROM products
    WHERE category_id = $1 AND status = 'active' AND id > $2
    ORDER BY id LIMIT 20"""
LIST_FILTER = """
    SELECT id, name, price FROM products
    WHERE category_id = $1 AND status = 'active' AND price BETWEEN $2 AND $3
    ORDER BY id LIMIT 20"""
WRITE = """
    WITH changed AS (
        UPDATE products SET price = price + 1, version = version + 1, updated_at = now()
        WHERE id = $1
        RETURNING id
    )
    INSERT INTO outbox (product_id, event_type, payload)
    SELECT id, 'product.updated', '{}' FROM changed"""

times = defaultdict(list)
errors = defaultdict(int)


def category():
    return 11 + int(90 * random.random() ** 2)


def next_read():
    dice = random.random()
    if dice < 0.60:
        return "get", GET, (random.randint(1, PRODUCTS),)
    if dice < 0.85:
        return "list_category", LIST_CATEGORY, (category(), random.randint(0, PRODUCTS))
    price = random.randint(1, 990)
    return "list_filter", LIST_FILTER, (category(), price, price + 10)


def next_write():
    return "write", WRITE, (random.randint(1, PRODUCTS),)


async def send(pool, name, query, args, scheduled):
    try:
        async with asyncio.timeout(TIMEOUT):
            async with pool.acquire() as conn: 
                await conn.fetch(query, *args)
        times[name].append((time.perf_counter() - scheduled) * 1000)
    except Exception:
        errors[name] += 1


async def traffic(pool, rate, next_request):
    tasks = []
    behind = 0.0
    start = time.perf_counter()
    for i in range(int(rate * SECONDS)):
        scheduled = start + i / rate
        delay = scheduled - time.perf_counter()
        if delay > 0:
            await asyncio.sleep(delay)
        behind = max(behind, -delay)
        tasks.append(asyncio.create_task(send(pool, *next_request(), scheduled)))
    await asyncio.gather(*tasks)
    if behind > 0.05:
        print(f"! client fell behind schedule by {behind * 1000:.0f} ms: do not trust {STAGE} {rate}", file=sys.stderr)


async def main():
    try:
        reads = await asyncpg.create_pool(os.environ["READ_URL"], min_size=POOL, max_size=POOL)
    except Exception as error:
        sys.exit(f"! {STAGE}: could not open {POOL} connections: {error}")
    writes = await asyncpg.create_pool(os.environ["WRITE_URL"], min_size=5, max_size=5)
    await asyncio.gather(traffic(reads, RATE, next_read), traffic(writes, WRITE_RATE, next_write))
    backends = await writes.fetchval(BACKENDS)  # real connections on the primary, while our pools are open
    await reads.close()
    await writes.close()

    if STAGE == "warmup":
        return
    for name in ["get", "list_category", "list_filter", "write"]:
        ms = times[name]
        q = statistics.quantiles(ms, n=100) if len(ms) > 1 else [0.0] * 99
        print(f"{STAGE},{RATE},{name},{len(ms)},{q[49]:.1f},{q[94]:.1f},{q[98]:.1f},{max(ms, default=0):.1f},{errors[name]},{backends}")


uvloop.run(main())