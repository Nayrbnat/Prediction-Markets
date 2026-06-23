"""POST /analyze — the headline endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.config import get_settings
from app.models.requests import AnalyzeRequest
from app.models.responses import TopicAnalysis
from app.services import analysis_service

router = APIRouter()


@router.post("/analyze", response_model=TopicAnalysis, tags=["analysis"])
async def analyze(request: Request, body: AnalyzeRequest) -> TopicAnalysis:
    return await analysis_service.analyze(
        body,
        repo=getattr(request.app.state, "repo", None),
        gateway=request.app.state.gateway,
        settings=get_settings(),
    )
