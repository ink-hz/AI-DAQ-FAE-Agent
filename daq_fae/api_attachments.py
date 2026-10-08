"""Local Dev HTTP attachment assembly; archive and model evidence are disabled."""
from contextlib import asynccontextmanager
from pathlib import Path

from src.api.attachment_limits import AttachmentUploadGate
from src.api.attachment_routes import register_attachment_routes
from src.attachments.archive_service import AttachmentArchiveService
from src.attachments.models import AttachmentLimits
from src.attachments.service import AttachmentService
from src.attachments.store import AttachmentGCWorker, AttachmentStore


def configure_attachments(app, *, root: Path, limits: AttachmentLimits, clock=None,
                          archive_repository=None, archive_enabled=False,
                          archive_handoff_seconds=604800):
    options = {'clock': clock} if clock is not None else {}
    store = AttachmentStore(root, limits, **options)
    store.gc_expired(archive_repository)
    worker = AttachmentGCWorker(store, lifecycle=archive_repository)
    app.state.attachment_store = store
    app.state.attachment_service = AttachmentService(store, limits)
    app.state.attachment_upload_gate = AttachmentUploadGate(
        max_concurrent=2, per_ip_batches_per_minute=20, trusted_proxy_hosts=(),
    )
    app.state.attachment_archive_service = AttachmentArchiveService(
        store, enabled=archive_enabled, handoff_seconds=archive_handoff_seconds,
        repository=archive_repository,
    )
    app.state.attachment_archive_repository = archive_repository
    app.state.attachment_gc_worker = worker
    register_attachment_routes(app)

    @asynccontextmanager
    async def lifespan(_app):
        worker.start()
        try:
            yield
        finally:
            worker.stop()

    app.router.lifespan_context = lifespan
