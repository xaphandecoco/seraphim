import asyncio
import logging

from app.config import legacy_settings
from app.database import async_session
from app.services.queue_manager import QueueManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def main():
    logger.info("Starting queue consumer (worker=%s)", legacy_settings.WORKER_ID)
    manager = QueueManager(db_session_factory=async_session)
    try:
        await manager.run()
    except asyncio.CancelledError:
        logger.info("Queue consumer cancelled")
    except Exception:
        logger.exception("Queue consumer encountered fatal error")
        raise


if __name__ == "__main__":
    asyncio.run(main())
