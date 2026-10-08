"""Restricted command surface for the private attachment archive handoff."""
from __future__ import annotations

import argparse
import json
import logging
import os
import secrets
import sys
from datetime import datetime
from time import monotonic
from typing import Callable, TextIO
from uuid import UUID

from src.attachments.archive_repository import (
    AttachmentArchiveError,
    AttachmentArchiveRepository,
)
from src.attachments.archive_service import AttachmentArchiveService
from src.attachments.models import AttachmentLimits
from src.attachments.store import AttachmentStore
from src.config import load_config

logger = logging.getLogger(__name__)


def _bounded_limit(value: str) -> int:
    number = int(value)
    if not 1 <= number <= 100:
        raise argparse.ArgumentTypeError("limit must be between 1 and 100")
    return number


def _uuid(value: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a UUID") from exc


def _rfc3339(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be RFC3339") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("must include a timezone")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fae-attachment-archive")
    actions = parser.add_subparsers(dest="action", required=True)

    list_parser = actions.add_parser("list")
    list_parser.add_argument("--limit", type=_bounded_limit, default=50)
    list_parser.add_argument("--cursor")

    read_parser = actions.add_parser("read")
    read_parser.add_argument("--relation-id", type=_uuid, required=True)
    read_parser.add_argument(
        "--variant", choices=("original", "thumbnail"), required=True
    )

    ack_parser = actions.add_parser("ack")
    ack_parser.add_argument("--relation-id", type=_uuid, required=True)
    ack_parser.add_argument(
        "--status", choices=("archived", "failed", "deleted"), required=True
    )
    ack_parser.add_argument("--sha256")
    ack_parser.add_argument("--platform-attachment-id", type=_uuid)
    ack_parser.add_argument("--archived-at", type=_rfc3339)
    ack_parser.add_argument("--error-code")
    return parser


def _service_from_config() -> AttachmentArchiveService:
    config = load_config()
    enabled = bool(getattr(config, "attachment_archive_enabled", False))
    handoff_seconds = int(
        getattr(config, "attachment_archive_handoff_seconds", 604800)
    )
    repository = (
        AttachmentArchiveRepository(config.database_url)
        if config.database_url
        else None
    )
    limits = AttachmentLimits(
        ttl_seconds=config.attachment_ttl_seconds,
        max_files=config.attachment_max_files,
        max_batch_bytes=config.attachment_max_batch_bytes,
        storage_capacity_bytes=config.attachment_storage_capacity_bytes,
    )
    return AttachmentArchiveService(
        AttachmentStore(config.attachment_storage_dir, limits),
        enabled=enabled,
        handoff_seconds=handoff_seconds,
        repository=repository,
    )


def _write_json(stream: TextIO, payload: dict) -> None:
    stream.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    stream.write("\n")


def _validate_ack(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    fields = {
        "sha256": args.sha256,
        "platform_attachment_id": args.platform_attachment_id,
        "archived_at": args.archived_at,
        "error_code": args.error_code,
    }
    required = {
        "archived": {"sha256", "platform_attachment_id", "archived_at"},
        "failed": {"error_code"},
        "deleted": {"platform_attachment_id"},
    }[args.status]
    present = {name for name, value in fields.items() if value is not None}
    if present != required:
        parser.error(f"{args.status} ack fields are invalid")
    if args.sha256 is not None and (
        len(args.sha256) != 64
        or any(char not in "0123456789abcdef" for char in args.sha256)
    ):
        parser.error("sha256 must be 64 lowercase hex characters")


def main(
    argv: list[str] | None = None,
    *,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
    service_factory: Callable[[], AttachmentArchiveService] = _service_from_config,
) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.action == "ack":
        _validate_ack(parser, args)
    request_id = f"archive-{secrets.token_hex(8)}"
    started_at = monotonic()
    result_code = "ok"
    ok = False
    try:
        service = service_factory()
        if args.action == "list":
            page = service.list_pending(limit=args.limit, cursor=args.cursor)
            for index, manifest in enumerate(page.items):
                payload = manifest.as_dict()
                payload["next_cursor"] = (
                    page.next_cursor if index == len(page.items) - 1 else None
                )
                _write_json(stdout, payload)
        elif args.action == "read":
            content = service.read(args.relation_id, args.variant)
            binary = getattr(stdout, "buffer", None)
            if binary is None:
                raise AttachmentArchiveError("archive_binary_output_required")
            binary.write(content)
            binary.flush()
        elif args.status == "archived":
            service.ack_archived(
                relation_id=args.relation_id,
                sha256=args.sha256,
                platform_attachment_id=args.platform_attachment_id,
                archived_at=args.archived_at,
            )
            _write_json(stdout, _ack_result(args))
        elif args.status == "failed":
            service.ack_failed(args.relation_id, args.error_code)
            _write_json(stdout, _ack_result(args))
        else:
            service.ack_deleted(args.relation_id, args.platform_attachment_id)
            _write_json(stdout, _ack_result(args))
        ok = True
        return 0
    except (AttachmentArchiveError, ValueError) as exc:
        code = getattr(exc, "code", "archive_request_invalid")
        result_code = code
        _write_json(stderr, {"ok": False, "code": code, "request_id": request_id})
        return 1
    except Exception:
        code = "archive_internal_error"
        result_code = code
        _write_json(stderr, {"ok": False, "code": code, "request_id": request_id})
        return 1
    finally:
        # Audit only governed metadata; never names or payload bytes.
        _audit(
            action=args.action,
            relation_id=getattr(args, "relation_id", None),
            ok=ok,
            result_code=result_code,
            duration_ms=int((monotonic() - started_at) * 1000),
        )


def _ack_result(args: argparse.Namespace) -> dict:
    return {
        "ok": True,
        "relation_id": str(args.relation_id),
        "status": args.status,
    }


def _audit(
    *,
    action: str,
    relation_id: UUID | None,
    ok: bool,
    result_code: str,
    duration_ms: int,
) -> None:
    raw_caller_id = os.getenv("FAE_ARCHIVE_CALLER_ID", "unknown")[:64]
    caller_id = (
        raw_caller_id
        if raw_caller_id
        and all(char.isalnum() or char in "-_.@" for char in raw_caller_id)
        else "unknown"
    )
    logger.info(
        "attachment_archive_command %s",
        json.dumps(
            {
                "action": action,
                "caller_id": caller_id,
                "duration_ms": duration_ms,
                "ok": ok,
                "relation_id": str(relation_id) if relation_id else None,
                "result_code": result_code,
            },
            separators=(",", ":"),
            sort_keys=True,
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())
