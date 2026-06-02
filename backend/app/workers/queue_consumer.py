import asyncio
import logging

from app.config import dynamic_settings, legacy_settings
from app.database import async_session
from app.services.queue_manager import QueueManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def main():
    logger.info("Starting queue consumer (worker=%s)", legacy_settings.WORKER_ID)

    # Initialize dynamic settings from DB before doing any work
    logger.info("Queue consumer: initializing settings from database…")
    try:
        async with async_session() as session:
            await dynamic_settings.initialize(session)
        logger.info("Queue consumer: settings initialized (civicrm_url=%r)",
                    dynamic_settings.get_civicrm_url())
    except Exception as exc:
        logger.error("Queue consumer: could not load settings: %s — proceeding with defaults", exc)

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
