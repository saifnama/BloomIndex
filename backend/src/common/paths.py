"""Repository filesystem root resolution and path utilities.

Anchors runtime paths relative to repository structure instead of
working directory to maintain consistency across container and service
environments.
"""

from pathlib import Path

_THIS_FILE = Path(__file__).resolve()


def repo_root() -> Path:
    """Return the absolute path to the repository root directory."""
    return _THIS_FILE.parent.parent.parent.parent


def tmp_dir() -> Path:
    """Return the runtime directory for temporary uploads and caches."""
    return repo_root() / "tmp"


def kb_dir() -> Path:
    """Return root directory for knowledge base storage and state."""
    return repo_root() / "knowledge_base"


def frontend_dist() -> Path:
    """Return the build output directory for the compiled frontend."""
    return repo_root() / "frontend" / "dist"


def safe_join(base: Path, *parts: str) -> Path | None:
    """Resolve and join path parts within base directory.

    Returns None if the resolved candidate escapes base boundaries.
    """
    candidate = (base.joinpath(*parts)).resolve()
    try:
        # Reject directory traversal attempts that escape the base root.
        candidate.relative_to(base.resolve())
    except ValueError:
        return None
    return candidate
