from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

from app.config import legacy_settings

engine = create_async_engine(
    legacy_settings.DATABASE_URL,
    echo=legacy_settings.ENVIRONMENT == "development",
    future=True,
)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
Base = declarative_base()


async def get_db() -> AsyncSession:
    async with async_session() as session:
        yield session
