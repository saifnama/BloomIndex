"""Dashboard router providing aggregated database metrics.

Exposes the /metrics endpoint returning KPIs and chart distributions
for the dashboard interface. All underlying queries are delegated to
backend.src.db.repository.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.db import repository
from backend.src.db.session import get_db

router = APIRouter(prefix="/api/dashboard", tags=["Dashboard"])


@router.get("/metrics")
async def get_dashboard_metrics(db: AsyncSession = Depends(get_db)):
    """Aggregated metrics for the homepage dashboard."""
    try:
        return await repository.dashboard_metrics(db)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
