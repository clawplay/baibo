"""Memory tools: read and write agent memory via the memory backend."""

from typing import Any

from nanobot.agent.memory_base import MemoryBackend
from nanobot.agent.tools.base import Tool


class SaveMemoryTool(Tool):
    """Tool to save information to daily memory notes."""

    def __init__(self, memory: MemoryBackend):
        self._memory = memory

    @property
    def name(self) -> str:
        return "save_memory"

    @property
    def description(self) -> str:
        return (
            "Save important information to today's memory notes. "
            "Use this to remember facts, preferences, decisions, or anything "
            "worth recalling in future conversations. Each call appends to today's notes."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "The information to remember (markdown formatted)",
                },
            },
            "required": ["content"],
        }

    async def execute(self, content: str, **kwargs: Any) -> str:
        try:
            await self._memory.append_today(content)
            return "Memory saved successfully."
        except Exception as e:
            return f"Error saving memory: {e}"


class UpdateLongTermMemoryTool(Tool):
    """Tool to update long-term memory."""

    def __init__(self, memory: MemoryBackend):
        self._memory = memory

    @property
    def name(self) -> str:
        return "update_long_term_memory"

    @property
    def description(self) -> str:
        return (
            "Update the long-term memory with consolidated information. "
            "This REPLACES the entire long-term memory content. "
            "Use this to store persistent facts like user preferences, "
            "important context, or summaries. Read current long-term memory first "
            "before updating to avoid losing existing information."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "The complete long-term memory content (markdown formatted)",
                },
            },
            "required": ["content"],
        }

    async def execute(self, content: str, **kwargs: Any) -> str:
        try:
            await self._memory.write_long_term(content)
            return "Long-term memory updated successfully."
        except Exception as e:
            return f"Error updating long-term memory: {e}"


class ReadMemoryTool(Tool):
    """Tool to read recent memories."""

    def __init__(self, memory: MemoryBackend):
        self._memory = memory

    @property
    def name(self) -> str:
        return "read_memory"

    @property
    def description(self) -> str:
        return (
            "Read memory contents. Can read today's notes, long-term memory, "
            "or recent memories from the past N days."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "scope": {
                    "type": "string",
                    "enum": ["today", "long_term", "recent"],
                    "description": "What to read: 'today' for today's notes, 'long_term' for persistent memory, 'recent' for last N days",
                },
                "days": {
                    "type": "integer",
                    "description": "Number of days to look back (only used when scope='recent', default 7)",
                },
            },
            "required": ["scope"],
        }

    async def execute(self, scope: str, days: int = 7, **kwargs: Any) -> str:
        try:
            if scope == "today":
                content = await self._memory.read_today()
                return content or "(No notes for today)"
            elif scope == "long_term":
                content = await self._memory.read_long_term()
                return content or "(No long-term memory)"
            elif scope == "recent":
                content = await self._memory.get_recent_memories(days)
                return content or f"(No memories in the last {days} days)"
            else:
                return f"Error: unknown scope '{scope}', use 'today', 'long_term', or 'recent'"
        except Exception as e:
            return f"Error reading memory: {e}"
