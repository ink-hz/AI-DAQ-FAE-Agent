from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping
from urllib.parse import urlencode
from urllib.request import urlopen


FAILURE_LAYERS = frozenset({
    "channel",
    "context",
    "guardrail",
    "schema",
    "planner",
    "capability_evidence",
    "coverage",
    "synthesis",
    "outcome",
    "trace_eval",
})
_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_SECRET_KEY_PARTS = (
    "api_key",
    "authorization",
    "credential",
    "database_url",
    "dsn",
    "password",
    "secret",
    "token",
)


class BatchManifestError(ValueError):
    pass


@dataclass(frozen=True)
class BatchIssue:
    issue_key: str
    title: str
    failure_layer: str
    secondary_layers: tuple[str, ...]
    expected_repair: str


@dataclass(frozen=True)
class BatchItem:
    turn_key: str
    issue_key: str


@dataclass(frozen=True)
class FeedbackClosureBatch:
    schema_version: int
    batch_id: str
    agent_id: str
    remediation_commit: str
    review_ref: str
    testset_ref: str
    issues: tuple[BatchIssue, ...]
    items: tuple[BatchItem, ...]


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise BatchManifestError(f"{name} must be an object")
    return value


def _string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BatchManifestError(f"{name} must be a non-empty string")
    return value


def _exact_keys(row: Mapping[str, Any], expected: set[str], name: str) -> None:
    missing = expected - set(row)
    extra = set(row) - expected
    if missing:
        raise BatchManifestError(f"{name} missing {sorted(missing)[0]}")
    if extra:
        raise BatchManifestError(f"{name} has unknown key {sorted(extra)[0]}")


def _reject_secret_like_keys(value: Any, path: str = "manifest") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).lower().replace("-", "_")
            if any(part in normalized for part in _SECRET_KEY_PARTS):
                raise BatchManifestError(f"secret-like key at {path}.{key}")
            _reject_secret_like_keys(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_secret_like_keys(child, f"{path}[{index}]")


def _relative_git_path(value: Any, name: str) -> str:
    path = _string(value, name)
    pure = PurePosixPath(path)
    if (
        "\\" in path
        or pure.is_absolute()
        or not pure.parts
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise BatchManifestError(f"{name} must be a relative Git path")
    return path


def _git_object_exists(repo_root: Path, object_name: str, label: str) -> None:
    result = subprocess.run(
        ["git", "-C", str(repo_root), "cat-file", "-e", object_name],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise BatchManifestError(f"{label} does not exist at remediation commit")


def _load_payload(source: Path | str | Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(source, Mapping):
        return source
    path = Path(source)
    try:
        return _mapping(json.loads(path.read_text(encoding="utf-8")), "manifest")
    except (OSError, json.JSONDecodeError) as error:
        raise BatchManifestError(f"cannot read manifest: {error}") from error


def load_batch_manifest(
    source: Path | str | Mapping[str, Any],
    repo_root: Path | str,
) -> FeedbackClosureBatch:
    payload = _load_payload(source)
    _reject_secret_like_keys(payload)
    _exact_keys(payload, {
        "schema_version",
        "batch_id",
        "agent_id",
        "remediation_commit",
        "review_ref",
        "testset_ref",
        "issues",
        "items",
    }, "manifest")
    if payload["schema_version"] != 1 or isinstance(payload["schema_version"], bool):
        raise BatchManifestError("schema_version must be 1")
    batch_id = _string(payload["batch_id"], "batch_id")
    if not _ID_PATTERN.fullmatch(batch_id):
        raise BatchManifestError("batch_id must be a lowercase stable key")
    agent_id = _string(payload["agent_id"], "agent_id")
    remediation_commit = _string(
        payload["remediation_commit"],
        "remediation_commit",
    )
    if not _SHA_PATTERN.fullmatch(remediation_commit):
        raise BatchManifestError("remediation_commit must be a full Git SHA")
    review_ref = _relative_git_path(payload["review_ref"], "review_ref")
    testset_ref = _relative_git_path(payload["testset_ref"], "testset_ref")

    raw_issues = payload["issues"]
    raw_items = payload["items"]
    if not isinstance(raw_issues, list) or not raw_issues:
        raise BatchManifestError("issues must be a non-empty list")
    if not isinstance(raw_items, list) or not raw_items:
        raise BatchManifestError("items must be a non-empty list")

    issues: list[BatchIssue] = []
    issue_keys: set[str] = set()
    for index, raw in enumerate(raw_issues):
        row = _mapping(raw, f"issues[{index}]")
        _exact_keys(row, {
            "issue_key",
            "title",
            "failure_layer",
            "secondary_layers",
            "expected_repair",
        }, f"issues[{index}]")
        issue_key = _string(row["issue_key"], f"issues[{index}].issue_key")
        if not _ID_PATTERN.fullmatch(issue_key):
            raise BatchManifestError("issue_key must be a lowercase stable key")
        if issue_key in issue_keys:
            raise BatchManifestError(f"duplicate issue_key: {issue_key}")
        issue_keys.add(issue_key)
        failure_layer = _string(
            row["failure_layer"],
            f"issues[{index}].failure_layer",
        )
        if failure_layer not in FAILURE_LAYERS:
            raise BatchManifestError(f"unknown failure_layer: {failure_layer}")
        raw_secondary = row["secondary_layers"]
        if not isinstance(raw_secondary, list) or not all(
            isinstance(layer, str) for layer in raw_secondary
        ):
            raise BatchManifestError("secondary_layers must be a string list")
        secondary = tuple(raw_secondary)
        if len(set(secondary)) != len(secondary):
            raise BatchManifestError("secondary_layers contains duplicates")
        if failure_layer in secondary or any(
            layer not in FAILURE_LAYERS for layer in secondary
        ):
            raise BatchManifestError("secondary_layers contains invalid failure_layer")
        issues.append(BatchIssue(
            issue_key=issue_key,
            title=_string(row["title"], f"issues[{index}].title"),
            failure_layer=failure_layer,
            secondary_layers=secondary,
            expected_repair=_string(
                row["expected_repair"],
                f"issues[{index}].expected_repair",
            ),
        ))

    items: list[BatchItem] = []
    turn_keys: set[str] = set()
    used_issue_keys: set[str] = set()
    for index, raw in enumerate(raw_items):
        row = _mapping(raw, f"items[{index}]")
        _exact_keys(row, {"turn_key", "issue_key"}, f"items[{index}]")
        turn_key = _string(row["turn_key"], f"items[{index}].turn_key")
        if not turn_key.startswith("fae:") or not turn_key.removeprefix("fae:"):
            raise BatchManifestError("turn_key must be fae: plus an opaque source key")
        if turn_key in turn_keys:
            raise BatchManifestError(f"duplicate turn_key: {turn_key}")
        turn_keys.add(turn_key)
        issue_key = _string(row["issue_key"], f"items[{index}].issue_key")
        if issue_key not in issue_keys:
            raise BatchManifestError(f"unknown issue_key: {issue_key}")
        used_issue_keys.add(issue_key)
        items.append(BatchItem(turn_key=turn_key, issue_key=issue_key))
    unused = issue_keys - used_issue_keys
    if unused:
        raise BatchManifestError(f"issue_key has no turns: {sorted(unused)[0]}")

    root = Path(repo_root).resolve()
    _git_object_exists(root, f"{remediation_commit}^{{commit}}", "remediation_commit")
    _git_object_exists(root, f"{remediation_commit}:{review_ref}", "review_ref")
    _git_object_exists(root, f"{remediation_commit}:{testset_ref}", "testset_ref")
    return FeedbackClosureBatch(
        schema_version=1,
        batch_id=batch_id,
        agent_id=agent_id,
        remediation_commit=remediation_commit,
        review_ref=review_ref,
        testset_ref=testset_ref,
        issues=tuple(issues),
        items=tuple(items),
    )


def _fetch_json(url: str) -> Any:
    with urlopen(url, timeout=10) as response:
        return json.load(response)


def validate_turns_api(
    batch: FeedbackClosureBatch,
    turns_api: str,
    *,
    fetch_json: Callable[[str], Any] = _fetch_json,
) -> None:
    query = urlencode(
        [("turn_key", item.turn_key) for item in batch.items],
        doseq=True,
    )
    url = f"{turns_api.rstrip('/')}/review/turn-summaries?{query}"
    rows = fetch_json(url)
    if not isinstance(rows, list):
        raise BatchManifestError("turns API did not return a list")
    returned: list[str] = []
    for row in rows:
        if not isinstance(row, Mapping) or not isinstance(row.get("turn_key"), str):
            raise BatchManifestError("turns API returned an invalid row")
        returned.append(row["turn_key"])
    if len(returned) != len(set(returned)):
        raise BatchManifestError("turns API returned duplicate source turns")
    expected = {item.turn_key for item in batch.items}
    missing = expected - set(returned)
    unexpected = set(returned) - expected
    if missing:
        raise BatchManifestError(f"missing source turn: {sorted(missing)[0]}")
    if unexpected:
        raise BatchManifestError(f"unexpected source turn: {sorted(unexpected)[0]}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a feedback closure batch")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).parents[2])
    parser.add_argument("--turns-api")
    args = parser.parse_args(argv)
    try:
        batch = load_batch_manifest(args.manifest, repo_root=args.repo_root)
        if args.turns_api:
            validate_turns_api(batch, args.turns_api)
    except BatchManifestError as error:
        parser.error(str(error))
    print(
        f"batch_id={batch.batch_id} issues={len(batch.issues)} "
        f"turns={len(batch.items)} validated=true"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
