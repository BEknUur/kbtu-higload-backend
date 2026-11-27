from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.config import settings


def _engine(url: str) -> AsyncEngine:
    return create_async_engine(
        url,
        pool_size=10,
        max_overflow=0,
        pool_timeout=settings.db_timeout,
        connect_args={"command_timeout": settings.db_timeout},
    )


engine = _engine(settings.database_url)
read_engine = _engine(settings.read_database_url or settings.database_url)

Session = async_sessionmaker(engine, expire_on_commit=False)
ReadSession = async_sessionmaker(read_engine, expire_on_commit=False)


async def dispose() -> None:
    await engine.dispose()
    await read_engine.dispose()
