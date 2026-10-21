"""Server-owned, hash-pinned Ed25519 approval verification for the D3 gate."""
from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from types import MappingProxyType
from typing import Callable, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


_SHA256 = re.compile(r'[0-9a-f]{64}')
_DOMAIN = b'daq-knowledge-release-approval/v1\0'
_INVALID_CONFIG = 'daq_knowledge_approval_configuration_invalid'


def approval_message(reviewer: str, payload_sha256: str) -> bytes:
    """Return the domain-separated bytes signed by an independent reviewer."""
    if (not isinstance(reviewer, str) or not reviewer.strip() or '\0' in reviewer
            or not isinstance(payload_sha256, str) or not _SHA256.fullmatch(payload_sha256)):
        raise ValueError('daq_knowledge_approval_message_invalid')
    try:
        return _DOMAIN + reviewer.encode('utf-8') + b'\0' + payload_sha256.encode('ascii')
    except UnicodeError:
        raise ValueError('daq_knowledge_approval_message_invalid') from None


def _decode_base64(value: str, length: int) -> bytes:
    if not isinstance(value, str):
        raise ValueError(_INVALID_CONFIG)
    decoded = base64.b64decode(value, validate=True)
    if len(decoded) != length or base64.b64encode(decoded).decode('ascii') != value:
        raise ValueError(_INVALID_CONFIG)
    return decoded


def _unique_json_object(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError(_INVALID_CONFIG)
        result[name] = value
    return result


@dataclass(frozen=True, slots=True)
class _ApprovalVerifier:
    keys: Mapping[str, Ed25519PublicKey]

    def __call__(self, reviewer, payload_sha256, signature) -> bool:
        try:
            message = approval_message(reviewer, payload_sha256)
            key = self.keys.get(reviewer)
            if key is None or not isinstance(signature, str) or not signature.startswith('ed25519:'):
                return False
            raw_signature = _decode_base64(signature[len('ed25519:'):], 64)
            key.verify(raw_signature, message)
            return True
        except (ValueError, TypeError, binascii.Error, InvalidSignature):
            return False


def load_approval_verifier(
    path: Path, expected_sha256: str,
) -> Callable[[str, str, str], bool]:
    """Load a frozen trust set; configuration failures abort startup without raw data.

    The pin is over exact file bytes. Changes take effect only after an explicit
    reload/restart with a newly reviewed pin; no online file lookup is performed.
    """
    try:
        if (not isinstance(path, Path) or not path.is_absolute()
                or not isinstance(expected_sha256, str) or not _SHA256.fullmatch(expected_sha256)
                or any(part.is_symlink() for part in (path, *path.parents))):
            raise ValueError(_INVALID_CONFIG)
        # O_NOFOLLOW closes the final-path symlink race; NONBLOCK avoids waiting
        # on a FIFO before fstat rejects anything other than a regular file.
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError(_INVALID_CONFIG)
            raw = stream.read()
        if hashlib.sha256(raw).hexdigest() != expected_sha256:
            raise ValueError(_INVALID_CONFIG)
        document = json.loads(raw, object_pairs_hook=_unique_json_object)
        if (not isinstance(document, dict)
                or set(document) != {'format_version', 'keys'}
                or document['format_version'] != 'daq-knowledge-approval-keys/v1'
                or not isinstance(document['keys'], list) or not document['keys']):
            raise ValueError(_INVALID_CONFIG)
        keys = {}
        for entry in document['keys']:
            if not isinstance(entry, dict) or set(entry) != {'reviewer', 'public_key_b64'}:
                raise ValueError(_INVALID_CONFIG)
            reviewer = entry['reviewer']
            approval_message(reviewer, '0' * 64)
            if reviewer in keys:
                raise ValueError(_INVALID_CONFIG)
            keys[reviewer] = Ed25519PublicKey.from_public_bytes(_decode_base64(entry['public_key_b64'], 32))
        return _ApprovalVerifier(MappingProxyType(keys))
    except (OSError, ValueError, TypeError, binascii.Error):
        raise ValueError(_INVALID_CONFIG) from None
