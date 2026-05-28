"""
Database initialization script.
Run once to create all tables from SQLAlchemy models.

Usage:
    cd backend
    python -m scripts.init_db

Or in Docker:
    docker-compose exec seraphim-backend python -m scripts.init_db
"""

import asyncio

from app.database import engine, Base
from app.models import *  # noqa: F401, F403


async def init():
    async with engine.begin() as conn:
        print("Creating tables...")
        await conn.run_sync(Base.metadata.create_all)
        print("Done.")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(init())
