from test_web_app import make_client


def test_review_page_wires_editor_overlay_and_synthetic_contract():
    response = make_client().get("/review?document_id=doc_passed")
    assert response.status_code == 200
    for expected in (
        'id="reviewEditorRoot"',
        'id="boxOverlayRoot"',
        "/static/app/review-editor.js",
        "/static/app/box-overlay.js",
        '"revision": 1',
        '"total": {"value": "1284.00"}',
        '"text": "Total 1,284.00"',
        '"document_id": "doc_passed"',
    ):
        assert expected in response.text


def test_demo_review_page_uses_same_editor_fixture():
    response = make_client().get("/demo/review")
    assert response.status_code == 200
    for expected in (
        "Review demo",
        'id="reviewEditorData"',
        "AI_UNCERTAIN",
        "DUPLICATE_DOCUMENT",
    ):
        assert expected in response.text
