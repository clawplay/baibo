"""Memory backend protocol for pluggable memory systems."""

from typing import Protocol, runtime_checkable


@runtime_checkable
class MemoryBackend(Protocol):
    """Protocol for memory backends (file, postgres, etc.)."""

    async def initialize(self) -> None:
        """Set up the backend (connect pool, ensure schema, etc.)."""
        ...

    async def close(self) -> None:
        """Tear down the backend (close pool, etc.)."""
        ...

    async def read_today(self) -> str:
        """Read today's memory notes."""
        ...

    async def append_today(self, content: str) -> None:
        """Append content to today's memory notes."""
        ...

    async def read_long_term(self) -> str:
        """Read long-term memory."""
        ...

    async def write_long_term(self, content: str) -> None:
        """Write to long-term memory."""
        ...

    async def get_recent_memories(self, days: int = 7) -> str:
        """Get memories from the last N days."""
        ...

    async def get_memory_context(self) -> str:
        """Get formatted memory context for the agent."""
        ...
