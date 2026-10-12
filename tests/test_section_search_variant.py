from daq_fae.knowledge.section_search import rank_sections


def test_explicit_variant_beats_generic_body_term_overlap():
    sections = [
        {"section_id": "product:ego-1600", "title": "EGO 双目 1600 × 1200 规格",
         "scope": {"product": "ego", "required_selectors": ["resolution_variant"],
                   "resolution_variant": "1600x1200"},
         "body": "EGO 1600 × 1200 的分辨率资料。", "dependency_claim_ids": []},
        {"section_id": "system:ego-wifi", "title": "EGO Wi-Fi PC 操作",
         "scope": {"product": "ego"},
         "body": "EGO 双目 1600 和 1920 两种配置的分辨率、Wi-Fi 版本需要确认。",
         "dependency_claim_ids": []},
    ]
    ranked = rank_sections(sections, [], "EGO 双目 1600 的分辨率、Wi-Fi 版本")
    assert ranked[0]["section_id"] == "product:ego-1600"
