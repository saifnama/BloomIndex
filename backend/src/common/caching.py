"""File-based caching utility with compression, versioning, and TTL.

Provides SimpleCache for storing API responses, entity extraction
results, and external metadata in compressed gzip files.
"""

import glob as _glob
import gzip
import hashlib
import json
import os
import time
from typing import Any

DEFAULT_TTL_SECONDS = 3600  # 1 hour default expiration.
MAX_CACHE_FILES = 500
CACHE_VERSION = "v3"  # Increment to invalidate stale cache formats.


class SimpleCache:
    """File-based cache with hash-prefixed subdirectories and gzip compression.

    Organizes cache entries using a two-character hex directory prefix
    to distribute filesystem inodes evenly. Enforces TTL expiration,
    format versioning, and LRU-style max-file eviction.
    """

    def __init__(
        self,
        cache_dir: str,
        ttl: int = DEFAULT_TTL_SECONDS,
        max_files: int = MAX_CACHE_FILES,
    ):
        self.cache_dir = cache_dir
        self.ttl = ttl
        self.max_files = max_files
        os.makedirs(self.cache_dir, exist_ok=True)

    def _get_key(self, identifier: str) -> str:
        return hashlib.sha256(identifier.encode()).hexdigest()

    def _get_path(self, key: str) -> str:
        """Return file path using the first two characters as a subdirectory."""
        prefix = key[:2]
        subdir = os.path.join(self.cache_dir, prefix)
        os.makedirs(subdir, exist_ok=True)
        return os.path.join(subdir, f"{key}.json.gz")

    def get(self, identifier: str) -> Any | None:
        key = self._get_key(identifier)
        cache_file = self._get_path(key)

        # Attempt to read compressed cache file.
        if os.path.exists(cache_file):
            try:
                with gzip.open(cache_file, "rt", encoding="utf-8") as f:
                    data = json.load(f)
                if self._is_valid(data):
                    return data
                # Remove expired or invalidated cache entry.
                os.remove(cache_file)
                self._cleanup_empty_dirs(os.path.dirname(cache_file))
            except Exception:
                pass

        # Attempt migration from legacy uncompressed json file.
        legacy_path = cache_file.replace(".json.gz", ".json")
        if os.path.exists(legacy_path):
            try:
                with open(legacy_path, encoding="utf-8") as f:
                    data = json.load(f)
                if self._is_valid(data):
                    self.set(identifier, data)
                    os.remove(legacy_path)
                    self._cleanup_empty_dirs(os.path.dirname(legacy_path))
                    return data
            except Exception:
                pass

        return None

    def delete(self, identifier: str) -> None:
        """Remove a specific cache entry and clean up empty parent directories."""
        key = self._get_key(identifier)
        cache_file = self._get_path(key)
        if os.path.exists(cache_file):
            os.remove(cache_file)
            self._cleanup_empty_dirs(os.path.dirname(cache_file))

    def _is_valid(self, data: dict) -> bool:
        """Verify that cache entry matches current version and TTL."""
        if data.get("_version") != CACHE_VERSION:
            return False
        created = data.get("_cached_at", 0)
        if created and (time.time() - created) > self.ttl:
            return False
        return True

    def set(self, identifier: str, data: Any):
        # Evict oldest files when storage capacity is exceeded.
        self._evict_if_needed()

        key = self._get_key(identifier)
        cache_file = self._get_path(key)

        # Wrap non-mapping payloads to ensure dictionary metadata merge.
        base: dict = data if isinstance(data, dict) else {"data": data}

        cache_data = {
            **base,
            "_version": CACHE_VERSION,
            "_cached_at": time.time(),
        }

        try:
            with gzip.open(cache_file, "wt", encoding="utf-8") as f:
                json.dump(cache_data, f, indent=2)
        except Exception:
            pass

    def _evict_if_needed(self):
        """Remove oldest cache files when file count reaches max_files."""
        files = sorted(
            _glob.glob(os.path.join(self.cache_dir, "**", "*.json*"), recursive=True),
            key=os.path.getmtime,
        )
        while len(files) >= self.max_files:
            oldest = files.pop(0)
            try:
                os.remove(oldest)
                self._cleanup_empty_dirs(os.path.dirname(oldest))
            except OSError:
                break

    def _cleanup_empty_dirs(self, dir_path: str):
        """Recursively remove empty directories up to the cache root."""
        while dir_path != self.cache_dir and dir_path.startswith(self.cache_dir):
            try:
                if not os.listdir(dir_path):
                    os.rmdir(dir_path)
                    dir_path = os.path.dirname(dir_path)
                else:
                    break
            except OSError:
                break


# Shared content-addressed cache instances for external API requests.
from backend.src.common.paths import tmp_dir as _tmp_dir_fn

_api_cache = os.path.join(os.fspath(_tmp_dir_fn()), "cache", "api")
pmc_cache = SimpleCache(
    os.path.join(_api_cache, "europepmc"),
    ttl=86400,  # 24 hours.
    max_files=500,
)
ner_cache = SimpleCache(
    os.path.join(_api_cache, "ner"),
    ttl=86400,  # 24 hours.
    max_files=500,
)
doi_cache = SimpleCache(
    os.path.join(_api_cache, "doi"),
    ttl=86400,  # 24 hours.
    max_files=500,
)
