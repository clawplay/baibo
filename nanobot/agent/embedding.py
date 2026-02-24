"""Embedding service wrapping litellm aembedding()."""


class EmbeddingService:
    """Generate embeddings via litellm (async)."""

    def __init__(
        self,
        model: str = "openai/text-embedding-3-small",
        dimensions: int = 1536,
        api_base: str | None = None,
        api_key: str | None = None,
    ):
        self.model = model
        self.dimensions = dimensions
        self.api_base = api_base
        self.api_key = api_key

    async def embed(self, text: str) -> list[float]:
        """Generate an embedding vector for a single text."""
        import litellm

        response = await litellm.aembedding(
            model=self.model,
            input=[text],
            dimensions=self.dimensions,
            api_base=self.api_base,
            api_key=self.api_key,
        )
        return response.data[0]["embedding"]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for multiple texts."""
        if not texts:
            return []

        import litellm

        response = await litellm.aembedding(
            model=self.model,
            input=texts,
            dimensions=self.dimensions,
            api_base=self.api_base,
            api_key=self.api_key,
        )
        return [item["embedding"] for item in response.data]
