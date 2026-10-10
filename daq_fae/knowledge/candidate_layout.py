"""Materialize a private camera-style DAQ knowledge view from governed candidates."""

from __future__ import annotations

import json
from collections import defaultdict
from copy import deepcopy
from pathlib import PurePosixPath

import yaml


def _json(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def _yaml(value) -> bytes:
    return yaml.safe_dump(value, allow_unicode=True, sort_keys=False, width=110).encode()


def _safe_path(name: str) -> bool:
    path = PurePosixPath(name)
    return (isinstance(name, str) and bool(name) and not path.is_absolute() and
            all(part not in {"", ".", ".."} for part in name.split("/")))


def _fact(row: dict) -> dict:
    data = row["data"]
    return {"record_id": row["id"], "status": row["status"],
            "field": data["field"], "value": deepcopy(data.get("value")),
            "raw_value": data.get("original_literal", data.get("source_label")),
            "source_literal": data.get("source_literal"),
            "unit": data.get("unit"), "comparator": data.get("comparator"),
            "conditions": deepcopy(data.get("conditions", {})),
            "scope": deepcopy(row["scope"]), "source_refs": deepcopy(row["source_refs"]),
            "original_record_id": data.get("original_record_id")}


def _source_index(entity: str, sections: list[dict], rows: list[dict],
                  archive_sources: dict[str, dict]) -> bytes:
    refs = [ref for section in sections for ref in section["source_refs"]]
    refs.extend(ref for row in rows for ref in row["source_refs"])
    by_source = defaultdict(set)
    for ref in refs:
        by_source[(ref["path"], ref["sha256"])].add(
            json.dumps(ref["locator"], ensure_ascii=False, sort_keys=True))
    lines = [f"# {entity} 来源与资产索引（候选）", "",
             "本页仅供私有原件复核；原件不在本知识目录内，资料可见和转发权限待签认。", "",
             "| 原件相对路径 | SHA-256 | 定位 | 归档范围 |",
             "| --- | --- | --- | --- |"]
    for (path, sha), locators in sorted(by_source.items()):
        escaped = path.replace("|", "\\|").replace("`", "\\`")
        scope = "本批原件" if archive_sources.get(path, {}).get("sha256") == sha else "外部候选"
        lines.append(f"| `{escaped}` | `{sha}` | " +
                     ", ".join(f"`{item}`" for item in sorted(locators)) + f" | {scope} |")
    lines.extend(["", "未建立到本产品的可靠关系的二进制资产保留在全局资产清单，不能从文件名推断规格或兼容性。", ""])
    return "\n".join(lines).encode()


def compose_layout(manifest_sha256: str, manifest: dict, disposition: dict,
                   assets: dict, groups: dict[str, tuple[dict, dict[str, bytes]]],
                   dictionary: dict, transcriptions: list[dict],
                   normalized: list[dict], vocabulary: dict,
                   graph: dict, audit: dict, software_matrix, link_ledger
                   ) -> tuple[dict[str, bytes], dict]:
    """Return private files; preserve candidate status and exact source identities."""
    source_rows = manifest["files"]
    source_map = {row["path"]: row for row in source_rows}
    if len(source_map) != len(source_rows):
        raise ValueError("duplicate original path")
    if (disposition["archive_manifest_sha256"] != manifest_sha256 or
            assets["archive_manifest_sha256"] != manifest_sha256):
        raise ValueError("A1 source inventory uses another archive")
    inventory = disposition["text_dispositions"] + assets["asset_inventory"]
    if (len(inventory) != len(source_rows) or
            {row["path"] for row in inventory} != set(source_map) or
            len({row["path"] for row in inventory}) != len(inventory) or
            any(row["sha256"] != source_map[row["path"]]["sha256"] for row in inventory)):
        raise ValueError("A1 inventory differs from original manifest")
    if set(groups) != {"a2", "a3", "a4"}:
        raise ValueError("all product, system and guidance groups are required")
    files: dict[str, bytes] = {}
    section_rows: list[dict] = []
    sections_by_product = defaultdict(list)
    product_entities = {}
    system_slugs = set()
    for group_name in ("a2", "a3", "a4"):
        index, documents = groups[group_name]
        if index["archive_manifest_sha256"] != manifest_sha256 or index.get("online_eligible") is not False:
            raise ValueError("chapter group differs from archive or is online eligible")
        expected_docs = {section["document"] for section in index["sections"]}
        if expected_docs != set(documents):
            raise ValueError("chapter document inventory differs from index")
        for section in index["sections"]:
            name = section["document"]
            if (not _safe_path(name) or name.split("/")[0] not in {"products", "systems", "topics"} or
                    section.get("review_status") != "candidate" or
                    section.get("fact_review") is not None or
                    section.get("permission_review") is not None or
                    section.get("view_roles") != [] or section.get("forward_roles") != []):
                raise ValueError("chapter is not a private unsigned candidate")
            if any(source_map.get(ref["path"], {}).get("sha256") != ref["sha256"]
                   for ref in section["source_refs"]):
                raise ValueError("chapter source differs from original archive")
            section_rows.append(deepcopy(section))
            parts = name.split("/")
            if parts[0] == "products":
                slug = parts[1]
                if group_name != "a2" or section["entity_id"] != "entity:" + slug:
                    raise ValueError("product chapter identity differs from directory")
                product_entities[slug] = section["entity_id"]
                sections_by_product[slug].append(section)
            elif parts[0] == "systems":
                system_slugs.add(parts[1])
        for name, body in documents.items():
            if not isinstance(body, bytes) or not body.strip() or name in files:
                raise ValueError("empty or duplicate candidate chapter")
            files[name] = body
    if len({row["section_id"] for row in section_rows}) != len(section_rows):
        raise ValueError("duplicate section identity")
    graph_sections = {row["section_id"]: row for row in graph["sections"]}
    if len(graph_sections) != len(graph["sections"]) or set(graph_sections) != {
            row["section_id"] for row in section_rows}:
        raise ValueError("chapter inventory differs from impact graph")
    for section in section_rows:
        graph_section = graph_sections[section["section_id"]]
        if any(section.get(key) != graph_section.get(key) for key in (
                "document", "title", "source_refs", "scope", "review_status",
                "fact_review", "permission_review", "view_roles", "forward_roles")):
            raise ValueError("chapter metadata differs from impact graph")

    old = dictionary["record_inventory"]
    if any(row.get("status") not in {"candidate", "conflict"} for row in old):
        raise ValueError("baseline contains an unexpected approved record")
    if any(row.get("kind") != "claim" or row.get("status") != "candidate"
           for row in normalized):
        raise ValueError("normalized facts must remain candidate claims")
    graph_by_id = {row["id"]: row for row in graph["records"]}
    if len(graph_by_id) != len(graph["records"]):
        raise ValueError("duplicate impact graph record")
    baseline_and_transcriptions = [*old, *transcriptions]
    all_input_ids = [row["id"] for row in [*baseline_and_transcriptions, *normalized]]
    if len(all_input_ids) != len(set(all_input_ids)) or set(all_input_ids) != set(graph_by_id):
        raise ValueError("knowledge record inventories differ from impact graph")
    for row in baseline_and_transcriptions:
        if graph_by_id[row["id"]] != row:
            raise ValueError("baseline or source transcription differs from graph")
    for row in transcriptions:
        if (row.get("status") != "candidate" or row.get("kind") not in {"claim", "procedure"} or
                any(source_map.get(ref["path"], {}).get("sha256") != ref["sha256"]
                    for ref in row["source_refs"])):
            raise ValueError("source transcription differs from original archive")
    for row in normalized:
        current = deepcopy(graph_by_id.get(row["id"]))
        if (not isinstance(current, dict) or
                current.pop("dependency_record_ids", None) != [row["data"]["original_record_id"]] or
                current != row or
                any(source_map.get(ref["path"], {}).get("sha256") != ref["sha256"]
                    for ref in row["source_refs"])):
            raise ValueError("normalized fact differs from source-bound review graph")
    field_ids = {row["field_id"] for row in vocabulary["fields"]}
    if (len(field_ids) != len(vocabulary["fields"]) or
            set(vocabulary["blocked_field_ids"]) != field_ids or
            any(row.get("vocabulary_status") != "candidate_review_pending"
                for row in vocabulary["fields"])):
        raise ValueError("field vocabulary review has approved or missing entries")
    claims = [row for row in [*old, *normalized] if row["kind"] == "claim"]
    for slug, entity in sorted(product_entities.items()):
        rows = [row for row in claims if row["data"].get("entity_id") == entity]
        prefix = f"products/{slug}/"
        facts = {"format_version": "daq-candidate-facts-v1", "status": "candidate",
                 "entity_id": entity, "source_manifest_sha256": manifest_sha256,
                 "facts": [_fact(row) for row in rows]}
        files[prefix + "facts.yaml"] = _yaml(facts)
        files[prefix + "sources_and_assets.md"] = _source_index(
            entity, sections_by_product[slug], rows, source_map)
        if prefix + "software.md" not in files:
            files[prefix + "software.md"] = (
                "# 软件资料缺口（候选）\n\n本批尚无该产品的独立软件章节；软件版本和兼容性待审核。\n").encode()
    unresolved = [row for row in claims if row["data"].get("entity_id") ==
                  "entity:ego-dual-unspecified"]
    files["_facts/ego-variant-unresolved.yaml"] = _yaml({
        "format_version": "daq-candidate-facts-v1", "status": "candidate",
        "identity_boundary": "generic EGO claims do not inherit to 1600x1200 or 1920x1200",
        "facts": [_fact(row) for row in unresolved]})
    for slug in sorted(system_slugs):
        prefix = f"systems/{slug}/"
        documents = sorted(name for name in files if name.startswith(prefix))
        topologies = {section.get("topology_id") for section in section_rows
                      if section["document"].startswith(prefix)} - {None}
        rows = [row for row in old if row["kind"] in {"topology", "procedure"} and
                (row["id"] in topologies or row.get("data", {}).get("topology_id") in topologies)]
        files[prefix + "records.yaml"] = _yaml({
            "format_version": "daq-candidate-system-records-v1", "status": "candidate",
            "topology_ids": sorted(topologies), "records": rows})
        files[prefix + "index.md"] = ("# " + slug + "（候选组合）\n\n" +
                                      "事实、步骤、角色与组合适用性尚未签认。\n\n" +
                                      "\n".join(f"- [{PurePosixPath(name).name}]({PurePosixPath(name).name})"
                                                for name in documents) + "\n").encode()
    topic_docs = sorted(name for name in files if name.startswith("topics/"))
    files["_topics/index.md"] = ("# 跨产品专题（候选）\n\n" +
                                  "\n".join(f"- [{PurePosixPath(name).stem}](../{name})"
                                            for name in topic_docs) + "\n").encode()
    selection_docs = sorted(name for name in files if name.startswith("systems/") and
                            name.endswith("/selection.md"))
    files["_selection/index.md"] = ("# 组合选型证据（候选）\n\n组合适用性不能由单件规格推出。\n\n" +
                                     "\n".join(f"- [{name.split('/')[1]}](../{name})"
                                               for name in selection_docs) + "\n").encode()
    files["_facts/catalog.yaml"] = _yaml({"status": "candidate",
                                            "names": dictionary["names"],
                                            "relations": dictionary["relations"],
                                            "ambiguities": dictionary["ambiguities"]})
    files["_facts/field_dictionary.yaml"] = _yaml({
        "status": "candidate_review_pending", "baseline_fields": dictionary["fields"],
        "proposed_fields": vocabulary["fields"]})
    files["_facts/coverage.yaml"] = _yaml({"status": "candidate",
                                             "coverage": graph["coverage"]})
    files["_facts/all-records.json"] = _json(graph["records"])
    files["_facts/source-transcriptions.json"] = _json(transcriptions)
    files["_sections/section-index.json"] = _json(section_rows)
    files["_sections/consistency-audit.json"] = _json(audit)
    files["_sources/source-disposition.json"] = _json(disposition)
    files["_sources/asset-inventory.json"] = _json(assets)
    asset_lines = ["# 原始资产目录（候选）", "",
                   "图片、软件包、固件、结构图和其他二进制仅登记身份；内容与权限均未审核。", "",
                   "| 类型 | 原件相对路径 | SHA-256 | 字节数 | 用途状态 |",
                   "| --- | --- | --- | ---: | --- |"]
    for row in sorted(assets["asset_inventory"], key=lambda item: item["path"]):
        escaped = row["path"].replace("|", "\\|").replace("`", "\\`")
        asset_lines.append(f"| {row['category']} | `{escaped}` | `{row['sha256']}` | "
                           f"{row['size']} | {row['content_review_status']} |")
    files["_sources/asset-index.md"] = ("\n".join(asset_lines) + "\n").encode()
    if (len({row["id"] for row in software_matrix}) != len(software_matrix) or
            any(row.get("status") != "candidate" or row.get("online_eligible") is not False or
                row.get("fact_review") != "pending" or
                row.get("permission_review") != "pending" or
                row.get("view_roles") != [] or row.get("forward_roles") != [] or
                not row.get("source_refs") or
                any(source_map.get(ref["path"], {}).get("sha256") != ref["sha256"]
                    for ref in row["source_refs"])
                for row in software_matrix)):
        raise ValueError("software evidence is not an original-bound private candidate")
    if (link_ledger.get("archive_manifest_sha256") != manifest_sha256 or
            link_ledger.get("approved_delivery_count") != 0 or
            link_ledger.get("candidate_count") != len(link_ledger["links"]) or
            len({row["id"] for row in link_ledger["links"]}) != len(link_ledger["links"]) or
            any(row.get("status") != "candidate" or row.get("answerable") is not False or
                row.get("link_review") is not None or row.get("fact_review") is not None or
                row.get("access_review") is not None for row in link_ledger["links"])):
        raise ValueError("link ledger is not an unsigned private candidate")
    for row in link_ledger["links"]:
        ref = row.get("source_ref", {})
        if "source_path" in ref and source_map.get(ref["source_path"], {}).get("sha256") != ref.get("source_sha256"):
            raise ValueError("link source differs from original archive")
    files["sdk/software-evidence-matrix.json"] = _json(software_matrix)
    files["links/candidate-link-ledger.json"] = _json(link_ledger)
    summary = {"format_version": "daq-private-camera-layout-v1", "status": "candidate",
               "online_eligible": False, "archive_manifest_sha256": manifest_sha256,
               "originals": len(source_rows), "text_originals": len(disposition["text_dispositions"]),
               "assets": len(assets["asset_inventory"]), "products": len(product_entities),
               "systems": len(system_slugs), "topic_documents": len(topic_docs),
               "sections": len(section_rows), "baseline_records": len(old),
               "source_transcriptions": len(transcriptions),
               "graph_records": len(graph["records"]),
               "normalized_candidate_claims": len(normalized),
               "blocked_fields": len(field_ids), "section_findings": len(audit["findings"]),
               "files": len(files) + 2}
    files["INDEX.md"] = ("# 数采 FAE 候选知识库\n\n" +
                         "按相机 FAE 的产品正文、事实、专题、选型及专门证据层组织；另设数采组合系统。\n" +
                         "全部内容为未签认候选，不能加载为在线知识。\n\n" +
                         "- `products/`：产品入口、定位、硬件、软件、事实和来源索引。\n" +
                         "- `systems/`：组合拓扑、准备、录制、排障及选型。\n" +
                         "- `topics/` 与 `_topics/`：跨产品专题。\n" +
                         "- `_facts/`：目录、字段、覆盖及完整候选记录。\n" +
                         "- `_selection/`：组合选型导航。\n" +
                         "- `sdk/`、`links/`：软件与链接候选证据。\n" +
                         "- `_sources/`：原件处置、资产清单及原件哈希清单。\n" +
                         "- `_sections/`：章节元数据和一致性发现。\n").encode()
    return files, summary
