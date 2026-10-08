from __future__ import annotations

import base64
import json
import os
import secrets
import stat
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from src.platform_tasks.models import TaskStoreError


@dataclass(frozen=True)
class SealedTaskContent:
    ciphertext: bytes = field(repr=False)
    key_version: int

    def __repr__(self) -> str:
        return f"SealedTaskContent(ciphertext=<redacted>, key_version={self.key_version!r})"


class TaskContentCodec:
    def __init__(self, *, active_key_version: int, keys: Mapping[int, bytes]) -> None:
        if (
            isinstance(active_key_version, bool)
            or active_key_version <= 0
            or active_key_version not in keys
            or any(isinstance(version, bool) or version <= 0 for version in keys)
            or any(not isinstance(key, bytes) or len(key) != 32 for key in keys.values())
        ):
            raise TaskStoreError("task_content_keyring_invalid")
        self._active_key_version = active_key_version
        self._keys = dict(keys)

    @classmethod
    def from_file(cls, path_value: str | Path) -> TaskContentCodec:
        path = Path(path_value)
        if not path.is_absolute():
            raise TaskStoreError("task_content_keyring_unavailable")
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(path, flags)
            try:
                metadata = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or stat.S_IMODE(metadata.st_mode) != 0o600
                    or metadata.st_uid != os.getuid()
                    or metadata.st_size > 64 * 1024
                ):
                    raise ValueError
                raw = b""
                while len(raw) <= 64 * 1024:
                    chunk = os.read(descriptor, 64 * 1024 + 1 - len(raw))
                    if not chunk:
                        break
                    raw += chunk
            finally:
                os.close(descriptor)
            document = json.loads(raw)
            if not isinstance(document, dict) or set(document) != {
                "active_version",
                "keys",
            }:
                raise ValueError
            active_version = document["active_version"]
            encoded_keys = document["keys"]
            if not isinstance(encoded_keys, dict) or not encoded_keys:
                raise ValueError
            keys = {
                int(version): base64.b64decode(value, validate=True)
                for version, value in encoded_keys.items()
            }
            return cls(active_key_version=active_version, keys=keys)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            raise TaskStoreError("task_content_keyring_unavailable") from None

    @property
    def active_key_version(self) -> int:
        return self._active_key_version

    def __repr__(self) -> str:
        return "TaskContentCodec(keys=<redacted>)"

    @staticmethod
    def _aad(subject: str, version: int) -> bytes:
        if not isinstance(subject, str) or not subject or "\0" in subject:
            raise ValueError
        return f"orbbec-fae-task:{subject}:v{version}".encode("utf-8")

    def seal_json(self, subject: str, value: dict[str, object]) -> SealedTaskContent:
        try:
            if not isinstance(value, dict):
                raise ValueError
            version = self._active_key_version
            plaintext = json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            nonce = secrets.token_bytes(12)
            ciphertext = nonce + AESGCM(self._keys[version]).encrypt(
                nonce, plaintext, self._aad(subject, version)
            )
            return SealedTaskContent(ciphertext=ciphertext, key_version=version)
        except (KeyError, TypeError, ValueError, UnicodeError):
            raise TaskStoreError("task_content_encrypt_failed") from None

    def unseal_json(
        self, subject: str, sealed: SealedTaskContent
    ) -> dict[str, object]:
        try:
            if not isinstance(sealed, SealedTaskContent) or len(sealed.ciphertext) < 28:
                raise ValueError
            key = self._keys[sealed.key_version]
            plaintext = AESGCM(key).decrypt(
                sealed.ciphertext[:12],
                sealed.ciphertext[12:],
                self._aad(subject, sealed.key_version),
            )
            value = json.loads(plaintext.decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError
            return value
        except (InvalidTag, KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError):
            raise TaskStoreError("task_content_decrypt_failed") from None
