"""DAQ-only role and release binding for durable Platform tasks."""
from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from urllib.parse import parse_qsl, urlsplit
from uuid import UUID

import psycopg
from src.platform_tasks.identity import TaskIdentityError
from src.platform_tasks.models import TaskStoreError

from daq_fae.authenticated_persistence import _validate_database
from daq_fae.platform_identity import AGENT_ID


class TaskKnowledgeAccessError(RuntimeError):
    """A sanitized, fail-closed task knowledge authorization reason."""


class TaskKnowledgeBindings:
    def __init__(self, database_url: str, *, entitlements, release_id: str,
                 active_release: Callable[[], str],
                 connect: Callable[[str], object] = psycopg.connect):
        try:
            _validate_database(database_url, None)
            query = parse_qsl(urlsplit(database_url).query, keep_blank_values=True)
        except (TypeError, ValueError):
            raise ValueError('daq_task_binding_database_invalid') from None
        if query != [('options', '-csearch_path=daq_fae')]:
            raise ValueError('daq_task_binding_database_invalid')
        if not callable(active_release) or not isinstance(release_id, str) or not release_id:
            raise ValueError('daq_task_binding_release_invalid')
        self.database_url = database_url
        self.entitlements = entitlements
        self.release_id = release_id
        self.active_release = active_release
        self._connect = connect

    def _role(self, requester_id: UUID) -> str:
        try:
            role = self.entitlements.role_for(SimpleNamespace(
                agent_id=AGENT_ID, active=True, subject_id=requester_id,
            ))
        except Exception:
            raise TaskKnowledgeAccessError('task_role_required') from None
        if not isinstance(role, str) or not role:
            raise TaskKnowledgeAccessError('task_role_required')
        return role

    def _current(self, requester_id: UUID) -> str:
        try:
            active = self.active_release()
        except Exception:
            raise TaskKnowledgeAccessError('task_release_unavailable') from None
        if active != self.release_id:
            raise TaskKnowledgeAccessError('task_release_changed')
        return self._role(requester_id)

    @staticmethod
    def _verify_database(connection) -> None:
        marker = connection.execute(
            """select current_schema(), agent_id,
                      database_name = current_database()
               from daq_installation_identity where singleton"""
        ).fetchone()
        if marker != ('daq_fae', AGENT_ID, True):
            raise TaskKnowledgeAccessError('task_database_identity_unverified')

    @staticmethod
    def _binding(connection, platform_task_id: UUID):
        return connection.execute(
            """select requester_internal_user_id, knowledge_role, knowledge_release
               from daq_task_knowledge_bindings where platform_task_id = %s""",
            (platform_task_id,),
        ).fetchone()

    def _match(self, row, requester_id: UUID, role: str) -> str:
        if row is None:
            raise TaskKnowledgeAccessError('task_binding_missing')
        if row[0] != requester_id:
            raise TaskKnowledgeAccessError('task_requester_changed')
        if row[2] != self.release_id:
            raise TaskKnowledgeAccessError('task_release_changed')
        if row[1] != role:
            raise TaskKnowledgeAccessError('task_role_changed')
        return role

    def assert_ready(self) -> None:
        """Refuse startup if an old queue task has no governed binding."""
        try:
            with self._connect(self.database_url) as connection:
                connection.execute('set transaction isolation level repeatable read read only')
                self._verify_database(connection)
                unbound = connection.execute(
                    """select exists (
                           select 1 from platform_tasks as task
                           left join daq_task_knowledge_bindings as binding
                             on binding.platform_task_id = task.platform_task_id
                           where binding.platform_task_id is null
                       )"""
                ).fetchone()[0]
                if unbound:
                    raise TaskKnowledgeAccessError('task_binding_missing')
        except psycopg.Error:
            raise TaskKnowledgeAccessError('task_binding_unavailable') from None

    def may_create(self, platform_task_id: UUID, requester_id: UUID) -> str:
        """Signed token preflight: unbound IDs are allowed only before task creation."""
        role = self._current(requester_id)
        try:
            with self._connect(self.database_url) as connection:
                connection.execute('set transaction isolation level repeatable read read only')
                self._verify_database(connection)
                row = self._binding(connection, platform_task_id)
                if row is not None:
                    return self._match(row, requester_id, role)
                exists = connection.execute(
                    'select exists (select 1 from platform_tasks where platform_task_id = %s)',
                    (platform_task_id,),
                ).fetchone()[0]
                if exists:
                    raise TaskKnowledgeAccessError('task_binding_missing')
        except psycopg.Error:
            raise TaskKnowledgeAccessError('task_binding_unavailable') from None
        return role

    def bind(self, platform_task_id: UUID, requester_id: UUID) -> str:
        """Reserve an immutable binding before the queue record can exist."""
        role = self.may_create(platform_task_id, requester_id)
        try:
            with self._connect(self.database_url) as connection:
                self._verify_database(connection)
                row = self._binding(connection, platform_task_id)
                if row is None:
                    exists = connection.execute(
                        'select exists (select 1 from platform_tasks where platform_task_id = %s)',
                        (platform_task_id,),
                    ).fetchone()[0]
                    if exists:
                        raise TaskKnowledgeAccessError('task_binding_missing')
                    connection.execute(
                        """insert into daq_task_knowledge_bindings
                           (platform_task_id, requester_internal_user_id,
                            knowledge_role, knowledge_release)
                           values (%s, %s, %s, %s) on conflict do nothing""",
                        (platform_task_id, requester_id, role, self.release_id),
                    )
                    row = self._binding(connection, platform_task_id)
                return self._match(row, requester_id, role)
        except psycopg.Error:
            raise TaskKnowledgeAccessError('task_binding_unavailable') from None

    def require_current(self, platform_task_id: UUID, requester_id: UUID) -> str:
        role = self._current(requester_id)
        try:
            with self._connect(self.database_url) as connection:
                connection.execute('set transaction isolation level repeatable read read only')
                self._verify_database(connection)
                return self._match(self._binding(connection, platform_task_id),
                                   requester_id, role)
        except psycopg.Error:
            raise TaskKnowledgeAccessError('task_binding_unavailable') from None


class BoundTaskStore:
    """Guard shared queue reads without changing its durable format."""

    def __init__(self, base, bindings: TaskKnowledgeBindings):
        self._base = base
        self._bindings = bindings

    def __getattr__(self, name):
        return getattr(self._base, name)

    def create_task(self, spec, *, now):
        try:
            self._bindings.bind(spec.platform_task_id, spec.requester_internal_user_id)
        except TaskKnowledgeAccessError as exc:
            raise TaskStoreError(str(exc)) from None
        return self._base.create_task(spec, now=now)

    def get_task(self, task_id):
        task = self._base.get_task(task_id)
        try:
            self._bindings.require_current(task.platform_task_id,
                                           task.requester_internal_user_id)
        except TaskKnowledgeAccessError as exc:
            raise TaskStoreError(str(exc)) from None
        return task

    def load_request(self, task_id):
        self.get_task(task_id)
        return self._base.load_request(task_id)

    def events_after(self, task_id, *, after, limit):
        self.get_task(task_id)
        return self._base.events_after(task_id, after=after, limit=limit)


class DaqTaskVerifier:
    """Recheck document entitlement before shared task routes serve any data."""

    def __init__(self, base, bindings: TaskKnowledgeBindings):
        self._base = base
        self._bindings = bindings

    def verify(self, token):
        identity = self._base.verify(token)
        if identity.agent_id != AGENT_ID:
            raise TaskIdentityError(
                status_code=403, code='scope_denied',
                message='task is not authorized',
            )
        try:
            self._bindings.may_create(identity.agent_task_id,
                                      identity.internal_user_id)
        except TaskKnowledgeAccessError as exc:
            unavailable = str(exc) in {'task_binding_unavailable',
                                        'task_release_unavailable'}
            raise TaskIdentityError(
                status_code=503 if unavailable else 403,
                code='upstream_unavailable' if unavailable else 'scope_denied',
                message='task authorization is unavailable' if unavailable
                        else 'task is not authorized',
            ) from None
        return identity
