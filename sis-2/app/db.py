from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings

engine = create_async_engine(
    settings.database_url,
    pool_size=10,
    max_overflow=0,
    pool_timeout=settings.db_timeout,                      
    connect_args={"command_timeout": settings.db_timeout}, 
)

Session = async_sessionmaker(engine, expire_on_commit=False)
