"""Field, table and evidence checks using the section 4 JSON shapes."""

from copy import deepcopy
from dataclasses import asdict

import pytest

from privasheet.validate.checks import Issue, check_extraction, check_review


@pytest.fixture
def documents():
    template = {
        "fields": [
            {
                "key": "invoice_no",
                "type": "text",
                "required": True,
                "key_label": True,
                "hint": {"labels": ["Invoice No", "Other"]},
            },
            {"key": "date", "type": "date", "required": True, "format": "DD/MM/YYYY"},
            {
                "key": "total",
                "type": "decimal",
                "required": True,
                "key_label": True,
                "hint": {"labels": ["Grand Total"]},
            },
            {"key": "po_no", "type": "text", "required": False},
        ],
        "tables": [
            {
                "key": "line_items",
                "required": True,
                "columns": [
                    {"key": "description", "type": "text"},
                    {"key": "qty", "type": "decimal"},
                    {
                        "key": "due",
                        "type": "date",
                        "format": "DD/MM/YYYY",
                        "required": False,
                    },
                ],
            }
        ],
        "match": {"min_key_label_ratio": 0.6},
    }
    texts = [
        "Invoice No: INV-0042",
        "01/09/2026",
        "Grand Total: 1,284.00",
        "Paper A4",
        "2",
    ]
    boxes = [
        {
            "id": f"p1-b{i:04}",
            "text": text,
            "score": 0.97,
            "quad": [[0, 0], [1, 0], [1, 1], [0, 1]],
        }
        for i, text in enumerate(texts)
    ]
    snapshot = {
        "snapshot_id": "sha256:synthetic",
        "engine": {
            "name": "rapidocr",
            "version": "3.9.2",
            "models": [],
            "config_sha256": "synthetic",
        },
        "pages": [{"page": 1, "width": 2480, "height": 3508, "boxes": boxes}],
    }

    def evidence(index, raw):
        return {
            "box_ids": [boxes[index]["id"]],
            "span": raw,
            "raw": raw,
            "value": "stale value must be ignored",
        }

    extracted = {
        "fields": {
            "invoice_no": evidence(0, "INV-0042"),
            "date": evidence(1, "01/09/2026"),
            "total": evidence(2, "1,284.00"),
            "po_no": {"missing": True},
        },
        "tables": {
            "line_items": [
                {
                    "description": evidence(3, "Paper A4"),
                    "qty": evidence(4, "2"),
                    "due": {"missing": True},
                }
            ]
        },
    }
    review = {
        "fields": {
            "invoice_no": "INV-0042",
            "date": "2026-09-01",
            "total": "1284.00",
            "po_no": None,
        },
        "tables": {
            "line_items": [{"description": "Paper A4", "qty": "2", "due": None}]
        },
        "acknowledged": {"revision": 3, "issues": []},
        "reviewed_at": "2026-09-17T10:00:00Z",
    }
    return template, snapshot, extracted, review


def pairs(issues):
    assert all(isinstance(issue, Issue) and issue.detail for issue in issues)
    return {(issue.code, issue.target) for issue in issues}


def test_issue_is_dataclass():
    assert asdict(Issue("PARSE_ERROR", "total", "Invalid decimal")) == {
        "code": "PARSE_ERROR",
        "target": "total",
        "detail": "Invalid decimal",
    }


def test_canonical_values_and_purity(documents):
    template, snapshot, extracted, review = documents
    before = deepcopy(documents)
    values, issues = check_extraction(template, snapshot, extracted)
    assert values == {key: review[key] for key in ("fields", "tables")}
    assert issues == []
    assert check_review(template, review) == []
    assert documents == before
    values["tables"]["line_items"][0]["qty"] = "99"
    assert documents == before


@pytest.mark.parametrize("target,raw", [("date", "31/02/2026"), ("total", "12,34")])
def test_parse_failure_is_not_missing(documents, target, raw):
    template, snapshot, extracted, _ = documents
    extracted["fields"][target]["raw"] = raw
    values, issues = check_extraction(template, snapshot, extracted)
    assert values["fields"][target] is None
    assert pairs(issues) == {("PARSE_ERROR", target)}


def test_table_parse_errors_and_text_as_is(documents):
    template, snapshot, extracted, _ = documents
    row = extracted["tables"]["line_items"][0]
    row["qty"]["raw"] = "NaN"
    row["due"] = {"box_ids": ["p1-b0001"], "span": "bad", "raw": "bad"}
    row["description"]["raw"] = "  Paper\tA4  "
    values, issues = check_extraction(template, snapshot, extracted)
    assert values["tables"]["line_items"][0] == {
        "qty": None,
        "due": None,
        "description": "  Paper\tA4  ",
    }
    assert pairs(issues) == {
        ("PARSE_ERROR", "line_items[0].qty"),
        ("PARSE_ERROR", "line_items[0].due"),
    }


@pytest.mark.parametrize("review_mode", [False, True])
def test_required_fields_and_columns(documents, review_mode):
    template, snapshot, extracted, review = documents
    data = review if review_mode else extracted
    missing = None if review_mode else {"missing": True}
    data["fields"]["date"] = missing
    data["tables"]["line_items"][0]["qty"] = missing
    if review_mode:
        issues = check_review(template, data)
    else:
        issues = check_extraction(template, snapshot, data)[1]
    assert ("REQUIRED_MISSING", "date") in pairs(issues)
    assert ("REQUIRED_MISSING", "line_items[0].qty") in pairs(issues)
    assert all(issue.code == "REQUIRED_MISSING" for issue in issues)


@pytest.mark.parametrize("required", [True, False])
@pytest.mark.parametrize("review_mode", [True, False])
def test_empty_table(documents, required, review_mode):
    template, snapshot, extracted, review = documents
    template["tables"][0]["required"] = required
    review["tables"]["line_items"] = []
    extracted["tables"]["line_items"] = []
    issues = (
        check_review(template, review)
        if review_mode
        else check_extraction(template, snapshot, extracted)[1]
    )
    assert pairs(issues) == (
        {("REQUIRED_MISSING", "line_items")} if required else set()
    )


def test_ungrounded_is_not_missing_or_parse_error(documents):
    template, snapshot, extracted, _ = documents
    extracted["fields"]["total"]["raw"] = None
    extracted["tables"]["line_items"][0]["qty"]["raw"] = None
    values, issues = check_extraction(template, snapshot, extracted)
    assert values["fields"]["total"] is None
    assert values["tables"]["line_items"][0]["qty"] is None
    assert pairs(issues) == {
        ("UNGROUNDED_VALUE", "total"),
        ("UNGROUNDED_VALUE", "line_items[0].qty"),
    }


@pytest.mark.parametrize(
    "score,expected", [(0.7999, True), (0.80, False), (0.99, False)]
)
def test_ocr_score_all_cited_boxes_all_pages(documents, score, expected):
    template, snapshot, extracted, _ = documents
    snapshot["pages"].append(
        {
            "page": 2,
            "width": 100,
            "height": 100,
            "boxes": [
                {
                    "id": "p2-b0000",
                    "text": "more",
                    "score": score,
                    "quad": [[0, 0], [1, 0], [1, 1], [0, 1]],
                }
            ],
        }
    )
    extracted["tables"]["line_items"][0]["qty"]["box_ids"].append("p2-b0000")
    snapshot["pages"][0]["boxes"].append(
        {"id": "p1-b0099", "text": "unused", "score": 0.1}
    )
    issues = check_extraction(template, snapshot, extracted)[1]
    assert pairs(issues) == (
        {("LOW_OCR_SCORE", "line_items[0].qty")} if expected else set()
    )


@pytest.mark.parametrize("same_span", [True, False])
def test_duplicate_uses_individual_box_and_span(documents, same_span):
    template, snapshot, extracted, _ = documents
    extracted["fields"]["po_no"] = {
        "box_ids": ["p1-b0000", "p1-b0003"],
        "span": "Paper A4" if same_span else "Paper",
        "raw": "Paper A4",
    }
    issues = check_extraction(template, snapshot, extracted)[1]
    if same_span:
        assert any(issue.code == "DUPLICATE_BOX" for issue in issues)
        assert all(
            issue.target in {"po_no", "line_items[0].description"} for issue in issues
        )
    else:
        assert issues == []


@pytest.mark.parametrize(
    "text,found",
    [
        ("  INVOICE\n NO: 42 ", True),
        ("invoice nx", True),
        ("invoice", True),  # ratio 14 / 17
        ("inv", False),
        ("Other", False),
        ("", False),
    ],
)
def test_template_label_normalization_similarity_and_first_label(
    documents, text, found
):
    template, snapshot, extracted, _ = documents
    snapshot["pages"][0]["boxes"][0]["text"] = text
    issues = check_extraction(template, snapshot, extracted)[1]
    assert [issue.code for issue in issues] == ([] if found else ["TEMPLATE_MISMATCH"])


@pytest.mark.parametrize("threshold,failed", [(0.5, False), (0.50001, True)])
def test_key_label_fraction_boundary(documents, threshold, failed):
    template, snapshot, extracted, _ = documents
    template["match"]["min_key_label_ratio"] = threshold
    snapshot["pages"][0]["boxes"][0]["text"] = "unrelated"
    assert bool(check_extraction(template, snapshot, extracted)[1]) is failed


@pytest.mark.parametrize(
    "key,value",
    [
        ("date", "01/09/2026"),
        ("date", "2026-02-29"),
        ("total", "$5"),
        ("total", "1,284.00"),
        ("total", 5),
        ("invoice_no", False),
    ],
)
def test_review_rejects_noncanonical_values(documents, key, value):
    template, _, _, review = documents
    review["fields"][key] = value
    assert pairs(check_review(template, review)) == {("PARSE_ERROR", key)}


def test_review_checks_cells_without_evidence(documents):
    template, _, _, review = documents
    review["tables"]["line_items"][0]["qty"] = "(2)"
    review["tables"]["line_items"][0]["due"] = "2026-09-17"
    assert pairs(check_review(template, review)) == {
        ("PARSE_ERROR", "line_items[0].qty")
    }
    review["tables"]["line_items"][0]["qty"] = "-2"
    review["fields"]["po_no"] = "typed by reviewer"
    assert check_review(template, review) == []


@pytest.mark.parametrize("text,expected", [("abcxy", False), ("abcdx", True)])
def test_similarity_exactly_eighty_percent(documents, text, expected):
    template, snapshot, extracted, _ = documents
    template["fields"][0]["hint"]["labels"] = ["abcde"]
    template["match"]["min_key_label_ratio"] = 1
    snapshot["pages"][0]["boxes"][0]["text"] = text
    assert (check_extraction(template, snapshot, extracted)[1] == []) is expected


def test_duplicate_across_rows_and_multiple_failures(documents):
    template, snapshot, extracted, _ = documents
    rows = extracted["tables"]["line_items"]
    rows.extend(deepcopy(rows[0]) for _ in range(2))
    rows[2]["qty"]["raw"] = "invalid"
    snapshot["pages"][0]["boxes"][4]["score"] = 0.1
    issues = check_extraction(template, snapshot, extracted)[1]
    assert {
        ("DUPLICATE_BOX", "line_items[2].qty"),
        ("LOW_OCR_SCORE", "line_items[2].qty"),
        ("PARSE_ERROR", "line_items[2].qty"),
    } <= pairs(issues)


@pytest.mark.parametrize("review_mode", [True, False])
def test_optional_column_can_be_missing_in_optional_table(documents, review_mode):
    template, snapshot, extracted, review = documents
    template["tables"][0]["required"] = False
    template["tables"][0]["columns"][1]["required"] = False
    extracted["tables"]["line_items"][0]["qty"] = {"missing": True}
    review["tables"]["line_items"][0]["qty"] = None
    issues = (
        check_review(template, review)
        if review_mode
        else check_extraction(template, snapshot, extracted)[1]
    )
    assert issues == []
    template["tables"][0]["columns"][1]["required"] = True
    issues = (
        check_review(template, review)
        if review_mode
        else check_extraction(template, snapshot, extracted)[1]
    )
    assert pairs(issues) == {("REQUIRED_MISSING", "line_items[0].qty")}
