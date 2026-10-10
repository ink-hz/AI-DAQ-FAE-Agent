"""Trusted, request-time DAQ document role mapping for the internal pilot."""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
from uuid import UUID

from src.platform_identity.models import PlatformIdentityError
from .records import ROLES

AGENT_ID = 'ai-daq-fae-agent'


class KnowledgeEntitlements:
    def __init__(self, path: Path):
        self.path = path
        self._read()

    def _read(self):
        try:
            if not self.path.is_absolute() or self.path.is_symlink():
                raise ValueError
            descriptor = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor) as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid not in {0, os.geteuid()} or info.st_mode & 0o022:
                    raise ValueError
                data = json.load(stream)
            if set(data) != {'agent_id', 'subjects'} or data['agent_id'] != AGENT_ID or not isinstance(data['subjects'], dict):
                raise ValueError
            for subject_id, role in data['subjects'].items():
                if str(UUID(subject_id)) != subject_id or not isinstance(role, str) or role not in ROLES:
                    raise ValueError
            return data['subjects']
        except (OSError, ValueError, TypeError, KeyError):
            raise PlatformIdentityError('daq_knowledge_role_contract_invalid', status_code=403) from None

    def role_for(self, subject):
        if subject is None or subject.agent_id != AGENT_ID or not subject.active:
            raise PlatformIdentityError('identity_binding_invalid', status_code=401)
        role = self._read().get(str(subject.subject_id))
        if role is None:
            raise PlatformIdentityError('daq_knowledge_role_required', status_code=403)
        return role

    def binding_for(self, subject, release_id):
        return {'role': self.role_for(subject), 'release_id': release_id}
