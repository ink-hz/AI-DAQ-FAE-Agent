"""HTTP ingress for temporary attachments; raw file download is intentionally absent."""
from __future__ import annotations

from typing import Annotated

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import JSONResponse, Response

from src.attachments.models import AttachmentError
from src.attachments.service import _public_manifest

_ERROR_STATUS = {
    "attachment_file_count_exceeded": 413,
    "attachment_batch_too_large": 413,
    "attachment_capacity_exceeded": 507,
    "attachment_not_found": 404,
    "attachment_expired": 410,
    "attachment_deleted": 410,
    "attachment_owner_mismatch": 403,
}


def _error(code: str, *, request_id: str, status_code: int | None = None) -> JSONResponse:
    messages = {
        "attachment_file_count_exceeded": "每批附件数量超过上限",
        "attachment_batch_too_large": "附件批次总大小超过上限",
        "attachment_capacity_exceeded": "临时附件容量不足",
        "attachment_not_found": "附件不存在",
        "attachment_expired": "附件已过期",
        "attachment_deleted": "附件已删除",
        "attachment_rate_limited": "附件上传过于频繁，请稍后重试",
        "attachment_upload_busy": "附件解析繁忙，请稍后重试",
    }
    return JSONResponse(
        {"code": code, "message": messages.get(code, "附件请求失败"), "request_id": request_id},
        status_code=status_code or _ERROR_STATUS.get(code, 422),
    )


def register_attachment_routes(app: FastAPI) -> None:
    @app.post("/attachments")
    async def upload_attachments(
        request: Request,
        files: Annotated[list[UploadFile], File()],
    ):
        service = request.app.state.attachment_service
        gate = request.app.state.attachment_upload_gate
        request_id = service.new_request_id()
        peer = request.client.host if request.client else "unknown"
        client_key = gate.client_key(peer, request.headers.get("x-forwarded-for"))
        lease = gate.try_acquire(client_key)
        if not lease.acquired:
            status = 429 if lease.reason == "attachment_rate_limited" else 503
            return _error(lease.reason or "attachment_upload_busy", request_id=request_id, status_code=status)
        try:
            subject = getattr(request.state, "platform_identity", None)
            result = await service.ingest_batch(
                files,
                request_id=request_id,
                owner_subject_id=(
                    str(subject.subject_id) if subject is not None else None
                ),
            )
            return JSONResponse(result.payload, status_code=201 if result.all_ok else 207)
        except AttachmentError as exc:
            return _error(exc.code, request_id=request_id)
        finally:
            lease.release()

    @app.get("/attachments/{attachment_id}")
    async def attachment_status(attachment_id: str, request: Request):
        try:
            manifest = request.app.state.attachment_store.get(attachment_id)
            _assert_attachment_owner(manifest, request)
        except AttachmentError as exc:
            return _error(exc.code, request_id="status")
        return _public_manifest(manifest)

    @app.delete("/attachments/{attachment_id}", status_code=204)
    async def delete_attachment(attachment_id: str, request: Request):
        try:
            manifest = request.app.state.attachment_store.get(attachment_id)
            _assert_attachment_owner(manifest, request)
        except AttachmentError as exc:
            if exc.code in {"attachment_not_found", "attachment_deleted"}:
                return Response(status_code=204)
            return _error(exc.code, request_id="delete")
        request.app.state.attachment_archive_service.delete(attachment_id)
        return Response(status_code=204)


def _assert_attachment_owner(manifest, request: Request) -> None:
    subject = getattr(request.state, "platform_identity", None)
    requester = str(subject.subject_id) if subject is not None else None
    if manifest.owner_subject_id != requester:
        raise AttachmentError("attachment_owner_mismatch")
