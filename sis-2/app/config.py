from pydantic_settings import BaseSettings,SettingsConfigDict

class Settings(BaseSettings):
    model_config=SettingsConfigDict(env_file=".env")

    database_url:str ="postgresql+asyncpg://catalog:catalog_dev@127.0.0.1:5433/catalog"
    redis_url:str="redis://127.0.0.1:6380/0"
     #into seconds
    redis_timeout: float = 0.010
    db_timeout: float = 0.050
    cache_ttl: int = 60 
    cachne_not_ttl: int =30
    cache_enabled: bool =True
    grpc_bind: str = "127.0.0.1:50051"


settings = Settings()