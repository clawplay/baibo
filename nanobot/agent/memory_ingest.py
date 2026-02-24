"""Conversation ingestor for auto-ingesting dialogue turns into memory."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Protocol

from loguru import logger

if TYPE_CHECKING:
    from nanobot.agent.memory_pg import PostgresMemoryStore


class ConversationIngestorBase(Protocol):
    """Protocol for conversation ingestors."""

    async def ingest(
        self, session_key: str, user_msg: str, assistant_msg: str
    ) -> None: ...


class NullIngestor:
    """No-op ingestor for non-postgres backends."""

    async def ingest(self, session_key: str, user_msg: str, assistant_msg: str) -> None:
        pass


class ConversationIngestor:
    """Ingests conversation turns into memory for semantic retrieval."""

    def __init__(self, pg_store: PostgresMemoryStore):
        self._store = pg_store

    async def ingest(self, session_key: str, user_msg: str, assistant_msg: str) -> None:
        """Insert user + assistant messages and enqueue embedding jobs."""
        table = self._store.conversation_table
        dims = self._store.dimensions

        try:
            async with self._store._pool.connection() as conn:
                for role, content in [("user", user_msg), ("assistant", assistant_msg)]:
                    if not content:
                        continue
                    row = await conn.execute(
                        f"INSERT INTO {table} (session_key, role, content) VALUES (%s, %s, %s) RETURNING id",
                        (session_key, role, content),
                    )
                    result = await row.fetchone()
                    record_id = result[0]

                    msg = json.dumps(
                        {
                            "table": table,
                            "id": record_id,
                            "content": f"{role}: {content}",
                            "dimensions": dims,
                        }
                    )
                    await conn.execute(
                        "SELECT pgmq.send('memory_embedding', %s::jsonb)", (msg,)
                    )

            logger.debug(f"Ingested conversation turn for {session_key}")
        except Exception as e:
            logger.warning(f"Failed to ingest conversation for {session_key}: {e}")
