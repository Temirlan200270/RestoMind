"""Long-cache для versioned static assets (?v=asset_ver)."""

from __future__ import annotations

from starlette.staticfiles import StaticFiles

_LONG_CACHE_SUFFIXES = (
    ".js",
    ".css",
    ".woff2",
    ".woff",
    ".png",
    ".svg",
    ".webp",
    ".ico",
    ".webmanifest",
)


class LongCacheStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        if response.status_code == 200 and path.endswith(_LONG_CACHE_SUFFIXES):
            response.headers.setdefault(
                "Cache-Control",
                "public, max-age=31536000, immutable",
            )
        return response
