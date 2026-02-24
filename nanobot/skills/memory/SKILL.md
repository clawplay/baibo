---
name: memory
description: Understand and use the memory system — auto-ingest conversation history, explicit memory tools, and semantic search.
---

# Memory System

You have a three-layer memory system. Two layers are automatic; one requires explicit tool calls.

## Architecture

```
Conversation Turn
    ├── auto-ingest → memory_conversation (raw dialogue, async embedding)
    ├── save_memory → memory_daily (agent-curated notes)
    └── update_long_term_memory → memory_long_term (persistent facts)

Recall: semantic_search → searches ALL three tables by embedding similarity
```

## 1. Auto-Ingest (Conversation Memory)

Every user + assistant message is **automatically** written to the conversation memory table after each turn. No tool call needed — this happens in the background.

- Stored as-is (no summarization, no LLM call)
- Embedding generated asynchronously via the background worker
- Searchable by semantic similarity alongside other memory sources
- Scoped by `session_key` (e.g. `telegram:12345`)

**When it helps:** Recalling exact wording from past conversations, finding context the agent didn't explicitly memorize.

**Config:** Controlled by `memory.auto_ingest` (default `true`). Set to `false` to disable.

## 2. Explicit Memory Tools

### `save_memory` — Daily Factual Notes

Append curated information to today's daily notes. Each call appends a new entry; entries are date-partitioned (one file per day).

```
save_memory(content="User prefers dark mode. Works at Anthropic.")
```

**Best scenarios:**
- User states a fact, preference, or decision: `save_memory(content="Prefers Python over JS for backend")`
- A task produces an actionable outcome: `save_memory(content="Deployed v2.1.0 to staging at 14:00")`
- User corrects something you got wrong: `save_memory(content="User's name is Ryan, not Brian")`
- Conversation reveals an important constraint: `save_memory(content="CI pipeline requires Node 20+")`

**Don't use for:**
- Raw dialogue (auto-ingest already handles this)
- Ephemeral information that won't matter tomorrow (e.g. "user said hello")
- Long verbatim text — distill it into key points first

### `update_long_term_memory` — Persistent Core Facts

Replace the **entire** long-term memory with consolidated, structured content. This is the user's persistent profile — things that matter across all sessions.

```
update_long_term_memory(content="## User Profile\n- Name: Ryan\n- Company: Anthropic\n...")
```

**Best scenarios:**
- After accumulating daily notes, consolidate into a structured profile
- User identity information: name, role, company, timezone, language preferences
- Enduring technical context: project stack, coding conventions, deployment targets
- Persistent preferences: communication style, tool preferences, recurring instructions

**Rules:**
1. Always `read_memory(scope="long_term")` first — this tool REPLACES all content
2. Merge new facts into existing content, don't discard what's already there
3. Keep it structured (use markdown headings, bullet lists) for easy retrieval
4. Remove outdated facts when you learn they've changed

### `read_memory` — Recall Stored Knowledge

Read back stored memories explicitly. Useful when semantic search hasn't surfaced what you need, or to review memory state before updating.

```
read_memory(scope="today")          # Today's daily notes
read_memory(scope="long_term")      # Persistent memory
read_memory(scope="recent", days=7) # Last N days of daily notes
```

**Best scenarios:**
- Before calling `update_long_term_memory` — always read first
- User asks "what do you remember about X" — check all scopes
- Debugging memory: verify whether something was saved correctly
- Reviewing recent context when semantic search results seem incomplete

## 3. Semantic Search

When the postgres backend is active with embeddings configured, every incoming user message triggers a semantic search across **all three memory tables** (daily, long_term, conversation). Relevant results are injected into the system prompt automatically.

This means:
- You don't need to call `read_memory` to recall past context — it's already in your prompt
- `save_memory` entries are more likely to surface because they're curated and concise
- Conversation memory provides raw dialogue fallback for anything not explicitly saved

## Best Practices

1. **Let auto-ingest handle the raw record.** Don't parrot back what the user said into `save_memory` — the original dialogue is already stored and searchable.
2. **Use `save_memory` for distilled insights.** "User is allergic to peanuts" is better than saving the entire conversation about food preferences.
3. **Treat long-term memory as a living document.** It's the user's persistent profile — keep it structured, up-to-date, and free of stale facts.
4. **Read before you write long-term.** Always `read_memory(scope="long_term")` before calling `update_long_term_memory` to avoid accidentally discarding existing content.
5. **Be proactive but not noisy.** When the user reveals something important (name, role, preferences, constraints), save it immediately. Don't save trivial greetings or ephemeral small talk.
