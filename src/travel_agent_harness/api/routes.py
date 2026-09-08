from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from ..models import TaskStatus
from .schemas import ApprovalRequest, ForkRequest, TripPlanRequest
from .service import QueueFullError, TaskService


def build_router(service: TaskService) -> APIRouter:
    router = APIRouter(prefix="/api")

    def get_task(task_id: str):
        try:
            return service.get(task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="任务不存在") from exc

    @router.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "component": "travel-agent-harness"}

    @router.get("/config")
    def config():
        return service.safe_config()

    @router.post("/plans", status_code=status.HTTP_202_ACCEPTED)
    def create_plan(request: TripPlanRequest):
        try:
            state = service.submit(request)
        except QueueFullError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
                headers={"Retry-After": "30"},
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return service.task_view(state)

    @router.get("/plans/{task_id}")
    def read_plan(task_id: str):
        return service.task_view(get_task(task_id))

    @router.get("/plans/{task_id}/trace")
    def read_trace(task_id: str, after: int = Query(default=0, ge=0)):
        try:
            events = service.trace(task_id, after_id=after)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="任务不存在") from exc
        return {"task_id": task_id, "events": events}

    @router.get("/plans/{task_id}/checkpoints")
    def read_checkpoints(task_id: str):
        try:
            checkpoints = service.checkpoints(task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="任务不存在") from exc
        return {"task_id": task_id, "checkpoints": checkpoints}

    @router.get("/plans/{task_id}/events")
    async def stream_events(request: Request, task_id: str, after: int = Query(default=0, ge=0)):
        get_task(task_id)

        async def event_source():
            cursor = after
            while not await request.is_disconnected():
                events = service.trace(task_id, after_id=cursor)
                for event in events:
                    cursor = event["id"]
                    yield f"id: {cursor}\nevent: trace\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                state = service.get(task_id)
                yield f"event: state\ndata: {json.dumps(service.task_view(state), ensure_ascii=False)}\n\n"
                if service.is_stream_terminal(state) or state.status == TaskStatus.WAITING_APPROVAL:
                    break
                await asyncio.sleep(0.65)

        return StreamingResponse(
            event_source(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.post("/plans/{task_id}/resume", status_code=status.HTTP_202_ACCEPTED)
    def resume_plan(task_id: str):
        get_task(task_id)
        try:
            state = service.resume(task_id)
        except QueueFullError as exc:
            raise HTTPException(status_code=503, detail=str(exc), headers={"Retry-After": "30"}) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return service.task_view(state)

    @router.post("/plans/{task_id}/fork", status_code=status.HTTP_202_ACCEPTED)
    def fork_plan(task_id: str, request: ForkRequest):
        get_task(task_id)
        try:
            state = service.fork(task_id, request.checkpoint_seq, run=request.run)
        except QueueFullError as exc:
            raise HTTPException(status_code=503, detail=str(exc), headers={"Retry-After": "30"}) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Checkpoint 不存在") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return service.task_view(state)

    @router.post(
        "/plans/{task_id}/approvals/{call_id}",
        status_code=status.HTTP_202_ACCEPTED,
    )
    def decide_approval(task_id: str, call_id: str, request: ApprovalRequest):
        get_task(task_id)
        try:
            state = service.decide(task_id, call_id, request.approved)
        except QueueFullError as exc:
            raise HTTPException(status_code=503, detail=str(exc), headers={"Retry-After": "30"}) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return service.task_view(state)

    return router
