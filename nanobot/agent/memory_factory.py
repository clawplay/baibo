"""Factory for creating memory backends based on configuration."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from nanobot.agent.memory_base import MemoryBackend
    from nanobot.agent.memory_ingest import ConversationIngestorBase
    from nanobot.config.schema import MemoryConfig


def create_memory_backend(
    workspace: Path,
    config: "MemoryConfig | None" = None,
) -> tuple["MemoryBackend", "ConversationIngestorBase"]:
    """
    Create the appropriate memory backend and conversation ingestor based on config.

    Returns a (backend, ingestor) tuple. File backend gets a NullIngestor;
    postgres backend gets a ConversationIngestor when auto_ingest is enabled.
    """
    from nanobot.agent.memory import MemoryStore
    from nanobot.agent.memory_ingest import NullIngestor

    if config is None or config.backend != "postgres":
        return MemoryStore(workspace), NullIngestor()

    if not config.postgres.dsn:
        logger.warning(
            "memory.backend=postgres but no DSN configured, falling back to file"
        )
        return MemoryStore(workspace), NullIngestor()

    try:
        from nanobot.agent.memory_pg import PostgresMemoryStore
    except ImportError:
        logger.warning("psycopg not installed, falling back to file memory backend")
        return MemoryStore(workspace), NullIngestor()

    store = PostgresMemoryStore(
        dsn=config.postgres.dsn,
        dimensions=config.embedding.dimensions,
        pool_min_size=config.postgres.pool_min_size,
        pool_max_size=config.postgres.pool_max_size,
        semantic_search_limit=config.semantic_search_limit,
    )

    # Attach embedding service for semantic search
    try:
        from nanobot.agent.embedding import EmbeddingService

        service = EmbeddingService(
            model=config.embedding.model,
            dimensions=config.embedding.dimensions,
            api_base=config.embedding.base_url,
            api_key=config.embedding.key,
        )
        store.set_embedding_service(service)
    except Exception as e:
        logger.warning(f"Could not create embedding service: {e}")

    # Create conversation ingestor
    if config.auto_ingest:
        from nanobot.agent.memory_ingest import ConversationIngestor

        ingestor: ConversationIngestorBase = ConversationIngestor(store)
    else:
        ingestor = NullIngestor()

    return store, ingestor


async def initialize_memory(backend: "MemoryBackend") -> None:
    """Initialize a memory backend (connect pool, ensure schema, etc.)."""
    await backend.initialize()


async def close_memory(backend: "MemoryBackend") -> None:
    """Close a memory backend (close pool, etc.)."""
    await backend.close()
