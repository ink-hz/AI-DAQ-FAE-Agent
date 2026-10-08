"""DAQ-only attachment archive handoff command.

The Platform archive worker invokes this command with DAQ credentials and a
dedicated DAQ database. It never loads the camera FAE config or agent ID.
"""
from __future__ import annotations

import os
from pathlib import Path

from src.attachments.archive_cli import main as archive_main
from src.attachments.archive_repository import AttachmentArchiveRepository
from src.attachments.archive_service import AttachmentArchiveService
from src.attachments.models import AttachmentLimits
from src.attachments.store import AttachmentStore

from daq_fae.authenticated_persistence import _validate_database
from daq_fae.platform_identity import AGENT_ID


def _service_from_daq_config() -> AttachmentArchiveService:
    flag = os.getenv("DAQ_ATTACHMENT_ARCHIVE_ENABLED", "false")
    if flag not in {"true", "false"}:
        raise ValueError("daq_attachment_archive_enabled_invalid")
    database_url = os.getenv("DAQ_DATABASE_URL", "")
    _validate_database(database_url, os.getenv("DATABASE_URL"))
    handoff_seconds = int(os.getenv("DAQ_ATTACHMENT_ARCHIVE_HANDOFF_SECONDS", "604800"))
    if handoff_seconds <= 0:
        raise ValueError("daq_attachment_archive_handoff_invalid")
    store = AttachmentStore(
        Path(os.getenv("DAQ_ATTACHMENT_STORAGE_DIR", str(Path(__file__).resolve().parents[1] / "data" / "daq_attachments"))),
        AttachmentLimits(),
    )
    return AttachmentArchiveService(
        store, enabled=(flag == "true"), handoff_seconds=handoff_seconds,
        repository=AttachmentArchiveRepository(database_url, agent_id=AGENT_ID),
    )


def main(argv: list[str] | None = None) -> int:
    return archive_main(argv, service_factory=_service_from_daq_config)


if __name__ == "__main__":
    raise SystemExit(main())
