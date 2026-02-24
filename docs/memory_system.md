# Memory System

nanobot uses a **hybrid memory** architecture combining two storage strategies and two retrieval strategies to provide efficient, scalable, and intelligent memory management.

## Architecture Overview

```
                        ┌──────────────────────────────────────┐
  Every turn            │  memory_conversation                 │
  (auto-ingest) ───────►│  raw user + assistant messages       │──┐
                        └──────────────────────────────────────┘  │
                        ┌──────────────────────────────────────┐  │  Semantic
  save_memory()         │  memory_daily                        │  ├─ Search
  (agent-curated) ─────►│  distilled facts, date-partitioned   │──┤  (pgvector)
                        └──────────────────────────────────────┘  │
                        ┌──────────────────────────────────────┐  │
  update_long_term      │  memory_long_term                    │  │
  _memory()       ─────►│  persistent user profile & context   │──┘
  (consolidated)        └──────────────────────────────────────┘
                                        │
                              async embedding worker
                              (pgmq background jobs)
```

## Storage Strategies

### 1. Async Full-Capture (Automatic)

**Table**: `memory_conversation`
- **Trigger**: Automatic, every conversation turn
- **Content**: Raw user and assistant messages as-is
- **Purpose**: Complete conversation history for analysis and retrieval
- **Performance**: Non-blocking, fire-and-forget insertion

This strategy ensures no conversation data is ever lost, with minimal impact on response latency.

### 2. Active Recording (Agent-Driven)

#### Daily Memory
**Table**: `memory_daily`
- **Trigger**: Agent calls `save_memory()` tool
- **Content**: Curated facts, insights, and important information
- **Organization**: Date-partitioned for efficient temporal queries
- **Purpose**: Structured knowledge that the agent deems important

#### Long-term Memory
**Table**: `memory_long_term`
- **Trigger**: Agent calls `update_long_term_memory()` tool
- **Content**: Consolidated user profile, preferences, and persistent context
- **Purpose**: Stable, long-term user understanding across sessions

## Retrieval Strategies

### 1. Semantic Search (Automatic)

Every incoming user message triggers semantic search across all three memory tables:

- **Technology**: pgvector cosine similarity on embeddings
- **Scope**: Simultaneous search across conversation, daily, and long-term memory
- **Ranking**: Results ranked by relevance score
- **Integration**: Top results automatically injected into system prompt

### 2. Explicit Lookup (On-demand)

The agent can explicitly query memory using the `read_memory()` tool:

- **Methods**: Keyword search, date range filtering, table-specific queries
- **Use Cases**: Targeted information retrieval, historical analysis
- **Control**: Agent decides when and what to search for

## Asynchronous Embedding Pipeline

Embeddings are generated asynchronously to maintain performance:

1. **Queue Creation**: New memory entries are added to `memory_embedding` queue (pgmq)
2. **Background Processing**: Worker polls queue and calls embedding API
3. **Vector Storage**: Generated embeddings are written back to memory rows
4. **Search Readiness**: Once embedded, content becomes available for semantic search

**Benefits**:
- Zero latency impact on conversation responses
- Resilient to embedding API failures
- Scalable queue-based processing

## Backend Options

### File Backend (Default)
- **Storage**: Markdown files in `memory/` directory
- **Setup**: Zero configuration
- **Features**: Daily and long-term memory only
- **Use Case**: Development, testing, simple deployments

### PostgreSQL Backend (Production)
- **Storage**: PostgreSQL tables with pgvector and pgmq extensions
- **Setup**: `docker/pg.Dockerfile` provided
- **Features**: Full hybrid memory with semantic search
- **Use Case**: Production deployments, large-scale usage

### Comparison

| Feature | File Backend | PostgreSQL Backend |
|---|---|---|
| Daily & long-term memory | ✅ | ✅ |
| Conversation auto-ingest | ❌ | ✅ |
| Semantic search | ❌ | ✅ |
| Async embeddings | ❌ | ✅ |
| Setup complexity | Minimal | Moderate |
| Scalability | Limited | High |

## Memory Management Best Practices

### For Users
- The system automatically captures all conversations
- Important information is identified and stored by the agent
- Long-term preferences build up over time for personalization

### For Developers
- Use the file backend for development and testing
- Switch to PostgreSQL backend for production deployments
- Monitor the embedding queue for processing delays
- Consider memory retention policies for long-running deployments

### For Agent Behavior
- Call `save_memory()` for important facts and insights
- Use `update_long_term_memory()` for persistent user preferences
- Leverage `read_memory()` for targeted historical queries
- Trust automatic semantic search for context retrieval
