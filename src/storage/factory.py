from __future__ import annotations

from src.attachments.archive_repository import AttachmentArchiveRepository
from src.config import Config
from src.storage.data_flywheel import DataFlywheelFallbackWriter, DisabledDataFlywheelStore
from src.storage.postgres_data_flywheel import PostgresDataFlywheelStore
from src.storage.review_center import DisabledReviewCenterStore, PostgresReviewCenterStore


def build_data_flywheel_store(config: Config):
    if not config.database_url:
        return DisabledDataFlywheelStore()
    return PostgresDataFlywheelStore(
        config.database_url,
        DataFlywheelFallbackWriter(config.data_flywheel_fallback_path),
    )


def build_review_center_store(config: Config):
    if not config.database_url:
        return DisabledReviewCenterStore()
    return PostgresReviewCenterStore(config.database_url)


def build_attachment_archive_repository(
    config: Config,
) -> AttachmentArchiveRepository | None:
    if config.attachment_archive_enabled and not config.database_url:
        raise ValueError("attachment_archive_requires_database_url")
    if not config.database_url:
        return None
    return AttachmentArchiveRepository(config.database_url)
