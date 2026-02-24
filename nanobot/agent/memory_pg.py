"""PostgreSQL memory backend with pgvector semantic search and pgmq async embedding."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from loguru import logger


class PostgresMemoryStore:
    """
    PostgreSQL-backed memory with pgvector + pgmq.

    Table names encode the embedding dimension (e.g. memory_daily_dim1536).
    Switching embedding models auto-creates new tables; old data is preserved.
    """

    def __init__(
        self,
        dsn: str,
        dimensions: int = 1536,
        pool_min_size: int = 2,
        pool_max_size: int = 10,
        semantic_search_limit: int = 10,
    ):
        self.dsn = dsn
        self.dimensions = dimensions
        self.pool_min_size = pool_min_size
        self.pool_max_size = pool_max_size
        self.semantic_search_limit = semantic_search_limit
        self._pool = None

        # Dimension-suffixed table names
        self.daily_table = f"memory_daily_dim{dimensions}"
        self.long_term_table = f"memory_long_term_dim{dimensions}"
        self.conversation_table = f"memory_conversation_dim{dimensions}"
        self.search_func = f"memory_search_dim{dimensions}"

    async def initialize(self) -> None:
        """Connect pool and ensure schema exists."""
        from psycopg_pool import AsyncConnectionPool

        self._pool = AsyncConnectionPool(
            conninfo=self.dsn,
            min_size=self.pool_min_size,
            max_size=self.pool_max_size,
            open=False,
        )
        await self._pool.open()
        await self._ensure_schema()
        logger.info(f"PostgresMemoryStore initialized (dim={self.dimensions})")

    async def close(self) -> None:
        """Close the connection pool."""
        if self._pool:
            await self._pool.close()
            self._pool = None

    async def _ensure_schema(self) -> None:
        """Create tables and indexes for the current dimension if they don't exist."""
        dim = self.dimensions
        async with self._pool.connection() as conn:
            await conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.daily_table} (
                    id          BIGSERIAL PRIMARY KEY,
                    entry_date  DATE NOT NULL DEFAULT CURRENT_DATE,
                    content     TEXT NOT NULL,
                    embedding   vector({dim}),
                    created_at  TIMESTAMPTZ DEFAULT now(),
                    updated_at  TIMESTAMPTZ DEFAULT now()
                )
            """
            )
            await conn.execute(
                f"""
                CREATE INDEX IF NOT EXISTS idx_{self.daily_table}_date
                    ON {self.daily_table} (entry_date DESC)
            """
            )
            await conn.execute(
                f"""
                CREATE INDEX IF NOT EXISTS idx_{self.daily_table}_embedding
                    ON {self.daily_table} USING hnsw (embedding vector_cosine_ops)
                    WITH (m=16, ef_construction=64)
            """
            )

            await conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.long_term_table} (
                    id          BIGSERIAL PRIMARY KEY,
                    content     TEXT NOT NULL,
                    embedding   vector({dim}),
                    version     INT NOT NULL DEFAULT 1,
                    created_at  TIMESTAMPTZ DEFAULT now()
                )
            """
            )

            # Conversation memory table
            await conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.conversation_table} (
                    id            BIGSERIAL PRIMARY KEY,
                    session_key   TEXT NOT NULL,
                    role          TEXT NOT NULL,
                    content       TEXT NOT NULL,
                    embedding     vector({dim}),
                    created_at    TIMESTAMPTZ DEFAULT now(),
                    updated_at    TIMESTAMPTZ DEFAULT now()
                )
            """
            )
            await conn.execute(
                f"""
                CREATE INDEX IF NOT EXISTS idx_{self.conversation_table}_session
                    ON {self.conversation_table} (session_key, created_at DESC)
            """
            )
            await conn.execute(
                f"""
                CREATE INDEX IF NOT EXISTS idx_{self.conversation_table}_embedding
                    ON {self.conversation_table} USING hnsw (embedding vector_cosine_ops)
                    WITH (m=16, ef_construction=64)
            """
            )

            # Dynamic search function across all memory tables
            await conn.execute(
                f"""
                CREATE OR REPLACE FUNCTION {self.search_func}(
                    query_embedding vector({dim}),
                    match_limit INT DEFAULT 10,
                    similarity_threshold FLOAT DEFAULT 0.3
                ) RETURNS TABLE (
                    source TEXT,
                    source_id BIGINT,
                    content TEXT,
                    entry_date DATE,
                    similarity FLOAT
                )
                LANGUAGE plpgsql AS $$
                BEGIN
                    RETURN QUERY
                    SELECT * FROM (
                        (SELECT
                            'daily'::TEXT AS source,
                            d.id AS source_id,
                            d.content,
                            d.entry_date,
                            (1 - (d.embedding <=> query_embedding))::FLOAT AS similarity
                        FROM {self.daily_table} d
                        WHERE d.embedding IS NOT NULL
                        ORDER BY d.embedding <=> query_embedding
                        LIMIT match_limit)
                        UNION ALL
                        (SELECT
                            'long_term'::TEXT AS source,
                            lt.id AS source_id,
                            lt.content,
                            NULL::DATE AS entry_date,
                            (1 - (lt.embedding <=> query_embedding))::FLOAT AS similarity
                        FROM {self.long_term_table} lt
                        WHERE lt.embedding IS NOT NULL
                        ORDER BY lt.embedding <=> query_embedding
                        LIMIT match_limit)
                        UNION ALL
                        (SELECT
                            'conversation'::TEXT AS source,
                            c.id AS source_id,
                            c.role || ': ' || c.content,
                            c.created_at::DATE AS entry_date,
                            (1 - (c.embedding <=> query_embedding))::FLOAT AS similarity
                        FROM {self.conversation_table} c
                        WHERE c.embedding IS NOT NULL
                        ORDER BY c.embedding <=> query_embedding
                        LIMIT match_limit)
                    ) combined
                    WHERE combined.similarity >= similarity_threshold
                    ORDER BY combined.similarity DESC
                    LIMIT match_limit;
                END;
                $$
            """
            )

    # ---- MemoryBackend interface ----

    async def read_today(self) -> str:
        """Read all entries for today."""
        async with self._pool.connection() as conn:
            rows = await conn.execute(
                f"SELECT content FROM {self.daily_table} WHERE entry_date = CURRENT_DATE ORDER BY id",
            )
            results = await rows.fetchall()
        if not results:
            return ""
        return "\n".join(row[0] for row in results)

    async def append_today(self, content: str) -> None:
        """Insert a daily entry and enqueue embedding job via pgmq."""
        async with self._pool.connection() as conn:
            row = await conn.execute(
                f"INSERT INTO {self.daily_table} (content) VALUES (%s) RETURNING id",
                (content,),
            )
            result = await row.fetchone()
            record_id = result[0]

            # Enqueue embedding job
            msg = json.dumps(
                {
                    "table": self.daily_table,
                    "id": record_id,
                    "content": content,
                    "dimensions": self.dimensions,
                }
            )
            await conn.execute(
                "SELECT pgmq.send('memory_embedding', %s::jsonb)", (msg,)
            )

    async def read_long_term(self) -> str:
        """Read the latest long-term memory entry."""
        async with self._pool.connection() as conn:
            row = await conn.execute(
                f"SELECT content FROM {self.long_term_table} ORDER BY version DESC LIMIT 1",
            )
            result = await row.fetchone()
        return result[0] if result else ""

    async def write_long_term(self, content: str) -> None:
        """Insert a new version of long-term memory and enqueue embedding job."""
        async with self._pool.connection() as conn:
            # Get next version
            row = await conn.execute(
                f"SELECT COALESCE(MAX(version), 0) + 1 FROM {self.long_term_table}",
            )
            result = await row.fetchone()
            next_version = result[0]

            row = await conn.execute(
                f"INSERT INTO {self.long_term_table} (content, version) VALUES (%s, %s) RETURNING id",
                (content, next_version),
            )
            result = await row.fetchone()
            record_id = result[0]

            msg = json.dumps(
                {
                    "table": self.long_term_table,
                    "id": record_id,
                    "content": content,
                    "dimensions": self.dimensions,
                }
            )
            await conn.execute(
                "SELECT pgmq.send('memory_embedding', %s::jsonb)", (msg,)
            )

    async def get_recent_memories(self, days: int = 7) -> str:
        """Get memories from the last N days."""
        cutoff = datetime.now().date() - timedelta(days=days)
        async with self._pool.connection() as conn:
            rows = await conn.execute(
                f"""
                SELECT entry_date, content
                FROM {self.daily_table}
                WHERE entry_date >= %s
                ORDER BY entry_date DESC, id
                """,
                (cutoff,),
            )
            results = await rows.fetchall()

        if not results:
            return ""

        # Group by date
        by_date: dict[str, list[str]] = {}
        for entry_date, content in results:
            key = str(entry_date)
            by_date.setdefault(key, []).append(content)

        parts = []
        for date_str, entries in by_date.items():
            parts.append(f"# {date_str}\n\n" + "\n".join(entries))
        return "\n\n---\n\n".join(parts)

    async def get_memory_context(self) -> str:
        """Get memory context (non-semantic, same as file backend)."""
        parts = []

        long_term = await self.read_long_term()
        if long_term:
            parts.append("## Long-term Memory\n" + long_term)

        today = await self.read_today()
        if today:
            parts.append("## Today's Notes\n" + today)

        return "\n\n".join(parts) if parts else ""

    # ---- Semantic search (postgres-only) ----

    async def semantic_search(
        self, query_embedding: list[float], limit: int | None = None
    ) -> list[dict]:
        """Search memories by embedding similarity."""
        limit = limit or self.semantic_search_limit
        vec_literal = "[" + ",".join(str(v) for v in query_embedding) + "]"
        async with self._pool.connection() as conn:
            rows = await conn.execute(
                f"SELECT source, source_id, content, entry_date, similarity FROM {self.search_func}(%s::vector, %s)",
                (vec_literal, limit),
            )
            results = await rows.fetchall()
        return [
            {
                "source": r[0],
                "source_id": r[1],
                "content": r[2],
                "entry_date": str(r[3]) if r[3] else None,
                "similarity": r[4],
            }
            for r in results
        ]

    async def get_memory_context_semantic(self, query: str) -> str:
        """
        Get memory context using semantic search.

        Falls back to regular get_memory_context if embedding service is not
        attached or the query embedding fails.
        """
        if not hasattr(self, "_embedding_service") or self._embedding_service is None:
            return await self.get_memory_context()

        try:
            query_embedding = await self._embedding_service.embed(query)
            results = await self.semantic_search(query_embedding)
        except Exception as e:
            logger.warning(f"Semantic search failed, falling back: {e}")
            return await self.get_memory_context()

        if not results:
            return await self.get_memory_context()

        parts = []
        # Always include long-term memory header
        long_term = await self.read_long_term()
        if long_term:
            parts.append("## Long-term Memory\n" + long_term)

        # Semantic results
        semantic_parts = []
        for r in results:
            date_info = f" ({r['entry_date']})" if r["entry_date"] else ""
            sim = f"{r['similarity']:.2f}"
            semantic_parts.append(
                f"- [{r['source']}{date_info} sim={sim}] {r['content']}"
            )

        if semantic_parts:
            parts.append(
                "## Relevant Memories (semantic)\n" + "\n".join(semantic_parts)
            )

        # Also include today's notes if not already covered
        today = await self.read_today()
        if today:
            parts.append("## Today's Notes\n" + today)

        return "\n\n".join(parts) if parts else ""

    def set_embedding_service(self, service) -> None:
        """Attach an EmbeddingService for semantic search."""
        self._embedding_service = service
