"""Static regression tests for Streamlit control boundaries."""

import ast
from pathlib import Path


APP_SOURCE = Path("app.py").read_text(encoding="utf-8")
APP_TREE = ast.parse(APP_SOURCE)


def _function(name: str) -> ast.FunctionDef:
    return next(
        node
        for node in APP_TREE.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def test_extraction_does_not_assert_evidence_checkboxes():
    source = ast.get_source_segment(APP_SOURCE, _function("apply_extraction")) or ""
    evidence_keys = {
        "evidence_incorporation",
        "evidence_representative_id",
        "evidence_passport",
        "evidence_meeting",
        "evidence_business_place",
        "evidence_bank",
        "evidence_cash_reason",
        "evidence_origin",
        "evidence_title",
    }

    for key in evidence_keys:
        assert f'st.session_state["{key}"] = True' not in source
    assert "AI_EVIDENCE_HINTS" in source


def test_every_report_download_is_gated_by_review_confirmation():
    download_calls = [
        node
        for node in ast.walk(APP_TREE)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "download_button"
    ]

    assert len(download_calls) == 3
    for call in download_calls:
        disabled = next(
            (keyword.value for keyword in call.keywords if keyword.arg == "disabled"),
            None,
        )
        assert isinstance(disabled, ast.Name)
        assert disabled.id == "exports_locked"


def test_extraction_targets_use_session_defaults_without_widget_values():
    extraction_targets = {
        "assessment_date_input",
        "supplier_ref_input",
        "supply_ref_input",
        "country_input",
        "invoice_value_input",
        "payment_method_input",
        "is_goods_input",
        "offshore_input",
    }
    found = set()

    for call in (node for node in ast.walk(APP_TREE) if isinstance(node, ast.Call)):
        keywords = {keyword.arg: keyword.value for keyword in call.keywords}
        key_node = keywords.get("key")
        if not isinstance(key_node, ast.Constant) or key_node.value not in extraction_targets:
            continue
        found.add(key_node.value)
        assert "value" not in keywords

    assert found == extraction_targets


def test_reviewer_omission_and_article_6_exception_are_explicit():
    collection_source = ast.get_source_segment(
        APP_SOURCE, _function("collect_conclusion")
    ) or ""
    render_source = ast.get_source_segment(APP_SOURCE, _function("render_outcome")) or ""

    assert "Reviewer name is required" in collection_source
    assert "A rationale is required" in collection_source
    assert "exception_available and outcome.supply_id is None" in render_source
