"""Persistent secret initialization with atomic creation.

Prevents race conditions between concurrent processes on first boot
using exclusive file creation ('xb'). Corrupted or empty files from
aborted writes are pruned and retried, and permissions are restricted to
the owner on supported filesystems.
"""

import os
import secrets
from pathlib import Path


def _restrict(path: Path) -> None:
    """Restrict file permissions to owner read/write where supported.

    Suppresses OSError on platforms such as Windows where POSIX file
    permission modes are unsupported or ignored.
    """
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def get_or_create_secret(path: str | Path, *, env_var: str, num_bytes: int = 32) -> bytes:
    """Retrieve secret from environment or atomically initialize file.

    Returns the decoded bytes of the secret, generating a cryptographic
    token on first access.
    """
    p = Path(path)
    override = os.getenv(env_var)
    if override:
        return override.encode("utf-8")
    p.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            with open(p, "xb") as handle:
                token = secrets.token_hex(num_bytes).encode("utf-8")
                handle.write(token)
            _restrict(p)
            return token
        except FileExistsError:
            pass
        data = p.read_bytes().strip() if p.exists() else b""
        if data:
            # Apply restrictive permissions to pre-existing file.
            _restrict(p)
            return data
        try:
            # Prune empty file left by interrupted creation.
            p.unlink()
        except OSError:
            pass
    raise OSError(f"could not initialize secret file {p}")
