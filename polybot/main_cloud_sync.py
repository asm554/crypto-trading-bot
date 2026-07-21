"""
Cloud-Sync Einstiegspunkt.

Spiegelt die lokale Paper-Trading-Datenbank in festen Abständen nach Supabase.
Läuft als eigener, unabhängiger Prozess neben den Trading-Bots:

    python -m polybot.main_cloud_sync
"""

from polybot.cli_env import apply_cli_env
apply_cli_env()

import asyncio
import logging
import logging.handlers
import os

from polybot.cloud_sync import is_configured, sync_once
from polybot.paper_db import init_db

os.makedirs("logs", exist_ok=True)
handler = logging.handlers.RotatingFileHandler(
    "logs/cloud_sync.log", maxBytes=10 * 1024 * 1024, backupCount=2
)
handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] SYNC: %(message)s"))
logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.addHandler(handler)
logger.addHandler(logging.StreamHandler())

INTERVAL_SEC = int(os.getenv("CLOUD_SYNC_INTERVAL_SEC", "30"))


async def main() -> None:
    await init_db()
    if not is_configured():
        logger.warning(
            "SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY nicht gesetzt — Cloud-Sync bleibt untätig."
        )
    while True:
        try:
            await sync_once()
        except Exception as e:
            logger.warning("Cloud-Sync-Durchlauf fehlgeschlagen: %s", e)
        await asyncio.sleep(INTERVAL_SEC)


if __name__ == "__main__":
    asyncio.run(main())
