"""Application HTTP exception hierarchy and response serialization."""

from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse


class ApiError(HTTPException):
    """Base API exception conveying structured machine-readable error codes."""

    def __init__(self, status_code: int, message: str, *, code: str = "error"):
        super().__init__(status_code=status_code, detail={"error": message, "code": code})


class NotFoundError(ApiError):
    """HTTP 404 entity not found exception."""

    def __init__(self, message: str = "Not found", *, code: str = "not_found"):
        super().__init__(404, message, code=code)


async def _api_error_handler(_request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.detail)


def install_handlers(app: FastAPI) -> None:
    """Register custom API exception handlers on the FastAPI application."""
    app.add_exception_handler(ApiError, _api_error_handler)
