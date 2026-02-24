"""Background worker that consumes pgmq messages and generates embeddings."""

from __future__ import annotations

import asyncio
import json

from loguru import logger

from nanobot.agent.embedding import EmbeddingService


class MemoryEmbeddingWorker:
    """
    Consumes jobs from the pgmq 'memory_embedding' queue.

    Each message contains {table, id, content, dimensions}.
    The worker generates an embedding and writes it back to the row.
    Failed messages reappear after visibility_timeout (30s) for automatic retry.
    """

    QUEUE_NAME = "memory_embedding"
    VISIBILITY_TIMEOUT = 30  # seconds before message reappears on failure

    def __init__(
        self,
        dsn: str,
        embedding_service: EmbeddingService,
        poll_interval: float = 2.0,
    ):
        self.dsn = dsn
        self.embedding = embedding_service
        self.poll_interval = poll_interval
        self._running = False
        self._task: asyncio.Task | None = None
        self._pool = None

    async def start(self) -> None:
        """Start the worker as a background task."""
        from psycopg_pool import AsyncConnectionPool

        self._pool = AsyncConnectionPool(
            conninfo=self.dsn,
            min_size=1,
            max_size=2,
            open=False,
        )
        await self._pool.open()
        self._running = True
        self._task = asyncio.create_task(self._run())
        logger.info("MemoryEmbeddingWorker started")

    async def stop(self) -> None:
        """Stop the worker gracefully."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._pool:
            await self._pool.close()
            self._pool = None
        logger.info("MemoryEmbeddingWorker stopped")

    async def _run(self) -> None:
        """Main poll loop."""
        while self._running:
            try:
                processed = await self._poll_once()
                if not processed:
                    await asyncio.sleep(self.poll_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Worker poll error: {e}")
                await asyncio.sleep(self.poll_interval)

    async def _poll_once(self) -> bool:
        """Read one message from the queue and process it. Returns True if a message was processed."""
        async with self._pool.connection() as conn:
            row = await conn.execute(
                "SELECT msg_id, message FROM pgmq.read(%s, %s, 1)",
                (self.QUEUE_NAME, self.VISIBILITY_TIMEOUT),
            )
            result = await row.fetchone()

        if result is None:
            return False

        msg_id, message = result
        # message is already a dict when psycopg parses jsonb
        if isinstance(message, str):
            message = json.loads(message)

        table = message["table"]
        record_id = message["id"]
        content = message["content"]

        try:
            embedding = await self.embedding.embed(content)
            vec_literal = "[" + ",".join(str(v) for v in embedding) + "]"

            async with self._pool.connection() as conn:
                await conn.execute(
                    f"UPDATE {table} SET embedding = %s::vector, updated_at = now() WHERE id = %s",
                    (vec_literal, record_id),
                )
                # Archive the processed message
                await conn.execute(
                    "SELECT pgmq.archive(%s, %s)", (self.QUEUE_NAME, msg_id)
                )

            logger.debug(f"Embedded {table}#{record_id} ({len(embedding)} dims)")
            return True

        except Exception as e:
            logger.warning(f"Failed to embed {table}#{record_id}: {e}")
            # Message will reappear after visibility_timeout for retry
            return False
