"""Regenerate DAQ SDK records from verified local originals."""

import argparse
import json
from pathlib import Path

from daq_fae.knowledge.sdk_catalog import _serialized, build_catalog


def _index(records: list[dict], key: str, title: str) -> str:
    grouped: dict[str, list[str]] = {}
    for row in records:
        for value in row.get(key, []):
            grouped.setdefault(str(value), []).append(row["kb_id"])
    lines = ["# " + title, "", "Generated from `SDK_Knowledge.jsonl`; source scope is recorded in each row.", ""]
    for value, ids in sorted(grouped.items(), key=lambda item: item[0].casefold()):
        lines.append(f"- **{value}**: {', '.join(sorted(ids))}")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--directory", type=Path, default=Path("Knowledge_SDK"))
    args = parser.parse_args()
    records, manifest = build_catalog(args.directory / "curated.json", args.archive)
    (args.directory / "SDK_Knowledge.jsonl").write_bytes(_serialized(records))
    (args.directory / "SDK_Knowledge_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    for filename, key, title in (
        ("topic_index.md", "topics", "SDK topic index"),
        ("symbol_index.md", "symbols", "SDK symbol index"),
        ("usage_task_index.md", "usage_tasks", "SDK usage task index"),
    ):
        (args.directory / filename).write_text(_index(records, key, title))
    package_rows: dict[str, list[dict]] = {}
    for row in records:
        package_rows.setdefault(row["package"], []).append(row)
    package_lines = ["# SDK package index", ""]
    for package, rows in sorted(package_rows.items()):
        package_lines.extend([f"## {package}", "",
                              f"{len(rows)} records; build {rows[0]['build']}; {rows[0]['platform']}.", ""])
        package_lines.extend(f"- {row['kb_id']}: {row['title']}" for row in rows)
        package_lines.append("")
    (args.directory / "package_index.md").write_text("\n".join(package_lines))
    source_lines = ["# SDK source member index", ""]
    for row in records:
        source_lines.append(f"## {row['kb_id']}")
        source_lines.append("")
        for ref in row["source_refs"]:
            source_lines.append(f"- {ref['member']}:{ref['start_line']}-{ref['end_line']} "
                                f"(member SHA-256 `{ref['member_sha256']}`)")
        source_lines.append("")
    (args.directory / "source_member_index.md").write_text("\n".join(source_lines))
    print(json.dumps({"records": len(records), "source_refs": manifest["source_ref_count"],
                      "catalog_sha256": manifest["records_sha256"]}))


if __name__ == "__main__":
    main()
