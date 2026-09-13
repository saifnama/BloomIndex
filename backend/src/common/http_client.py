"""Shared asynchronous HTTP client manager.

Provides lifecycle management and connection pooling for outbound HTTP
requests across services.
"""

import httpx


class HttpClientManager:
    """Manage a shared httpx.AsyncClient singleton."""

    _client: httpx.AsyncClient | None = None

    @classmethod
    async def get_client(cls) -> httpx.AsyncClient:
        """Return active client, initializing lazily if needed."""
        if cls._client is None or cls._client.is_closed:
            # Fall back to lazy initialization when outside lifespan.
            cls._client = httpx.AsyncClient(
                timeout=30.0,
                limits=httpx.Limits(
                    max_connections=20,
                    max_keepalive_connections=5,
                    keepalive_expiry=30,
                ),
            )
        return cls._client

    @classmethod
    async def reset_client(cls):
        """Discard active client to recreate connection pool.

        Forces get_client to create fresh sockets on the next call
        after network errors.
        """
        if cls._client and not cls._client.is_closed:
            try:
                await cls._client.aclose()
            except Exception:
                pass
        cls._client = None

    @classmethod
    async def close_client(cls):
        """Close active client and release pooled connections."""
        if cls._client:
            await cls._client.aclose()
            cls._client = None
