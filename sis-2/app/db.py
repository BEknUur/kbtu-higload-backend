import json 
import asyncpg

from app.config import settings 


pool:asyncpg.Pool | None = None


async def _init(conn:asyncpg.Connection) ->None:
    await conn.set_type_codec("jsonb",encoder=json.dumps,
                              decoder=json.loads,schema="pg_catalog")


async def connect()->None:
    global pool 
    pool = await asyncpg.create_pool(
        settings.database_url,
        min_size=2,
        max_size=10,
        command_timeout=settings.db_timeout,
        init=_init,
    )

async def close() ->None:
    if pool:
        await pool.close()



def acquire():
    return pool.acquire(timeout=settings.db_timeout)