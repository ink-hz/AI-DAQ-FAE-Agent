"""Build a private, unsigned DAQ knowledge tree from verified original candidates."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from daq_fae.knowledge.candidate_layout import compose_layout
from daq_fae.knowledge.section_consistency import audit_sections
from scripts.archive_candidate_snapshot import verify_archive


def _load(path: Path):
    raw = path.read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def _checked(path: Path, expected: str):
    value, actual = _load(path)
    if actual != expected:
        raise ValueError("candidate input hash mismatch: " + path.name)
    return value, actual


def _private_outdir(path: Path) -> None:
    target = path.resolve()
    ancestor = target
    while not ancestor.exists():
        ancestor = ancestor.parent
    result = subprocess.run(["git", "-C", str(ancestor), "rev-parse", "--show-toplevel"],
                            capture_output=True, text=True, check=False)
    if result.returncode:
        raise ValueError("candidate knowledge output must be inside a repository")
    root = Path(result.stdout.strip()).resolve()
    if not target.is_relative_to(root / "knowledge" / "drafts"):
        raise ValueError("candidate knowledge output must be under knowledge/drafts")
    ignored = subprocess.run(["git", "-C", str(root), "check-ignore", "-q", "--no-index",
                              "--", str(target)], capture_output=True, check=False)
    if ignored.returncode:
        raise ValueError("candidate knowledge output is not Git-ignored")


def _documents(root: Path, index: dict) -> dict[str, bytes]:
    documents = {}
    for name in {row["document"] for row in index["sections"]}:
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("unsafe chapter document path")
        path = (root / relative).resolve()
        if not path.is_relative_to(root.resolve()) or path.is_symlink():
            raise ValueError("chapter document outside candidate root")
        documents[name] = path.read_bytes()
    return documents


def _write_private(path: Path, raw: bytes) -> str:
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
    return hashlib.sha256(raw).hexdigest()


def build(args) -> dict:
    if args.outdir.exists() or args.outdir.is_symlink():
        raise FileExistsError(args.outdir)
    _private_outdir(args.outdir)
    source = verify_archive(args.archive, args.expected_manifest_sha256)
    manifest_raw = (args.archive / "manifest.json").read_bytes()
    manifest = json.loads(manifest_raw)
    input_hashes = {"archive_manifest": source["manifest_sha256"]}
    disposition, input_hashes["source_disposition"] = _load(
        args.a1_dir / "source-disposition.json")
    assets, input_hashes["asset_inventory"] = _load(args.a1_dir / "asset-inventory.json")
    groups = {}
    for name, root in (("a2", args.a2_dir), ("a3", args.a3_dir), ("a4", args.a4_dir)):
        index, input_hashes[name + "_section_index"] = _load(root / "section-index.json")
        if index.get("source_disposition_sha256") != input_hashes["source_disposition"]:
            raise ValueError("chapter index differs from A1 source disposition")
        groups[name] = (index, _documents(root, index))
        input_hashes[name + "_documents"] = {
            path: hashlib.sha256(body).hexdigest() for path, body in groups[name][1].items()}
    dictionary, input_hashes["b1_dictionary"] = _load(args.b1_dictionary)
    b2_summary, input_hashes["b2_summary"] = _load(args.b2_review_dir / "summary.json")
    if b2_summary["baseline_dictionary_sha256"] != input_hashes["b1_dictionary"]:
        raise ValueError("B2 review differs from B1 dictionary")
    transcriptions, input_hashes["b2_original_transcriptions"] = _checked(
        args.b2_originals, b2_summary["originals_sha256"])
    normalized, input_hashes["normalized_records"] = _checked(
        args.b2_review_dir / "candidate-records.json",
        b2_summary["output_sha256"]["candidate-records.json"])
    vocabulary, input_hashes["field_vocabulary"] = _checked(
        args.b2_review_dir / "field-vocabulary-review.json",
        b2_summary["output_sha256"]["field-vocabulary-review.json"])
    b3_summary, input_hashes["b3_summary"] = _load(args.b3_review_dir / "summary.json")
    if (b3_summary["input_sha256"]["review_summary"] != input_hashes["b2_summary"] or
            b3_summary["input_sha256"]["candidate-records.json"] != input_hashes["normalized_records"] or
            b3_summary["input_sha256"]["field-vocabulary-review.json"] != input_hashes["field_vocabulary"]):
        raise ValueError("B3 graph differs from B2 review")
    graph, input_hashes["b3_graph"] = _checked(
        args.b3_review_dir / "dependency-bundle.json",
        b3_summary["output_sha256"]["dependency-bundle.json"])
    audit, input_hashes["b3_section_audit"] = _checked(
        args.b3_review_dir / "section-consistency-audit.json",
        b3_summary["output_sha256"]["section-consistency-audit.json"])
    bound_index, input_hashes["bound_section_index"] = _checked(
        args.bound_section_index, b3_summary["input_sha256"]["bound_section_index"])
    if (len({row["section_id"] for row in bound_index["sections"]}) != len(bound_index["sections"]) or
            {row["section_id"] for row in bound_index["sections"]} != {
            row["section_id"] for row in graph["sections"]}):
        raise ValueError("bound section inventory differs from review graph")
    if audit_sections(graph["sections"], bound_index["bodies"],
                      graph["records"], graph["snapshot"]) != audit:
        raise ValueError("section audit differs from source-bound graph")
    software_matrix, input_hashes["software_matrix"] = _load(args.c3_matrix)
    link_ledger, input_hashes["link_ledger"] = _load(args.c4_ledger)
    if link_ledger.get("a1_disposition_sha256") != input_hashes["source_disposition"]:
        raise ValueError("C4 link ledger differs from A1 source disposition")
    files, summary = compose_layout(
        source["manifest_sha256"], manifest, disposition, assets, groups, dictionary,
        transcriptions, normalized, vocabulary, graph, audit, software_matrix, link_ledger)
    files["_sources/manifest.json"] = manifest_raw
    if summary["files"] != len(files):
        raise ValueError("candidate layout file inventory mismatch")
    os.umask(0o077)
    args.outdir.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".daq-knowledge-stage-", dir=args.outdir.parent))
    try:
        output_hashes = {}
        for name, raw in sorted(files.items()):
            output_hashes[name] = _write_private(stage / name, raw)
        result = {**summary, "input_sha256": input_hashes, "output_sha256": output_hashes}
        _write_private(stage / "summary.json", (
            json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode())
        for name, digest in output_hashes.items():
            if hashlib.sha256((stage / name).read_bytes()).hexdigest() != digest:
                raise ValueError("candidate output changed during staging")
        if args.outdir.exists() or args.outdir.is_symlink():
            raise FileExistsError(args.outdir)
        stage.rename(args.outdir)
        return result
    except Exception:
        if stage.exists():
            shutil.rmtree(stage)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--expected-manifest-sha256", required=True)
    parser.add_argument("--a1-dir", type=Path, required=True)
    parser.add_argument("--a2-dir", type=Path, required=True)
    parser.add_argument("--a3-dir", type=Path, required=True)
    parser.add_argument("--a4-dir", type=Path, required=True)
    parser.add_argument("--b1-dictionary", type=Path, required=True)
    parser.add_argument("--b2-originals", type=Path, required=True)
    parser.add_argument("--b2-review-dir", type=Path, required=True)
    parser.add_argument("--b3-review-dir", type=Path, required=True)
    parser.add_argument("--bound-section-index", type=Path, required=True)
    parser.add_argument("--c3-matrix", type=Path, required=True)
    parser.add_argument("--c4-ledger", type=Path, required=True)
    parser.add_argument("--outdir", type=Path, required=True)
    result = build(parser.parse_args())
    print(json.dumps({key: result[key] for key in (
        "products", "systems", "topic_documents", "sections", "normalized_candidate_claims",
        "blocked_fields", "section_findings", "online_eligible")}, ensure_ascii=False,
        sort_keys=True))


if __name__ == "__main__":
    main()
