-- nanobot memory backend: PostgreSQL + pgvector + pgmq
-- This runs once on container init.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgmq CASCADE;

-- Create the embedding job queue
SELECT pgmq.create('memory_embedding');
