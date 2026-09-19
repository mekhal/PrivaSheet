import test from "node:test";
import assert from "node:assert/strict";

import {
  actionState,
  acknowledgementKey,
  buildDuplicateHref,
  buildReviewPayload,
  cancelDraft,
  createDraft,
  draftIssues,
  markRejected,
  markReviewed,
  saveDraft,
} from "../../src/privasheet/web/static/app/review-editor-logic.mjs";

function fixture(overrides = {}) {
  return {
    document_id: "doc_current",
    status: "needs_review",
    revision: 1,
    schema: {
      fields: [
        { key: "invoice_no", label: "Invoice number", type: "string", required: true },
        { key: "date", label: "Date", type: "date", required: true },
        { key: "total", label: "Total", type: "decimal", required: true },
        { key: "tax", label: "Tax", type: "decimal", required: false },
        { key: "due_date", label: "Due date", type: "date", required: false },
      ],
      tables: [
        {
          key: "line_items",
          label: "Line items",
          columns: [
            { key: "description", label: "Description", type: "string", required: true },
            { key: "amount", label: "Amount", type: "decimal", required: true },
          ],
        },
      ],
    },
    extracted: {
      fields: {
        invoice_no: { value: "INV-0042" },
        date: { value: "2026-09-18" },
        total: { value: "1284.00" },
        tax: { value: null },
        due_date: { value: null },
      },
      tables: {
        line_items: [
          {
            description: { value: "Synthetic service" },
            amount: { value: "1284.00" },
          },
        ],
      },
    },
    issues: [
      {
        code: "AI_UNCERTAIN",
        target: "fields.total",
        message: "Low confidence total.",
      },
      {
        code: "DUPLICATE_DOCUMENT",
        target: "document",
        document_id: "doc_passed",
        message: "Looks like an earlier document.",
      },
    ],
    review: null,
    ...overrides,
  };
}

function acknowledged(result) {
  return result.issues.map((issue, index) => acknowledgementKey(issue, index));
}

test("initial draft reads canonical editable values", () => {
  const draft = createDraft(fixture());

  assert.equal(draft.fields.date, "2026-09-18");
  assert.equal(draft.fields.total, "1284.00");
  assert.equal(draft.tables.line_items[0].amount, "1284.00");
  assert.deepEqual(draftIssues(fixture(), draft), []);
});

test("canonical validation rejects grouped decimals only when explicitly edited in", () => {
  const result = fixture();
  const draft = createDraft(result);
  draft.fields.total = "1,284.00";

  assert.deepEqual(draftIssues(result, draft), [
    {
      path: "fields.total",
      message: "Total must be a plain decimal like 1284.00.",
    },
  ]);
});

test("canonical validation rejects invalid dates", () => {
  const result = fixture();
  const draft = createDraft(result);
  draft.fields.date = "09/18/2026";

  assert.deepEqual(draftIssues(result, draft), [
    {
      path: "fields.date",
      message: "Date must use YYYY-MM-DD.",
    },
  ]);
});

test("optional blank decimal and date become null in review payload", () => {
  const result = fixture();
  const draft = createDraft(result);
  draft.fields.tax = "";
  draft.fields.due_date = "";

  assert.equal(buildReviewPayload(result, draft, acknowledged(result)).fields.tax, null);
  assert.equal(buildReviewPayload(result, draft, acknowledged(result)).fields.due_date, null);
});

test("review payload includes acknowledgement revision and unique issue keys", () => {
  const result = fixture({
    issues: [
      { code: "AI_UNCERTAIN", target: "fields.total", message: "First." },
      { code: "AI_UNCERTAIN", target: "fields.total", message: "Second." },
    ],
  });

  assert.notEqual(acknowledgementKey(result.issues[0], 0), acknowledgementKey(result.issues[1], 1));
  assert.deepEqual(buildReviewPayload(result, createDraft(result), acknowledged(result)).acknowledged, {
    revision: 1,
    issues: ["0:AI_UNCERTAIN:fields.total", "1:AI_UNCERTAIN:fields.total"],
  });
});

test("duplicate document issue links to earlier document id", () => {
  assert.equal(
    buildDuplicateHref({ code: "DUPLICATE_DOCUMENT", document_id: "doc_passed" }),
    "/review?document_id=doc_passed",
  );
  assert.equal(buildDuplicateHref({ code: "DUPLICATE_DOCUMENT" }), null);
});

test("save from every editable status moves to needs_review and enables mark reviewed", () => {
  for (const status of ["passed", "reviewed", "rejected", "needs_review"]) {
    const result = fixture({ status });
    const next = saveDraft(result, createDraft(result), acknowledged(result));

    assert.equal(actionState(status, false, [], true).canSave, true);
    assert.equal(next.status, "needs_review");
    assert.equal(next.editing, true);
    assert.equal(actionState(next.status, true, [], true).canMarkReviewed, true);
  }
});

test("cancel drops draft without changing status, stored values, or review", () => {
  const result = fixture({
    status: "reviewed",
    review: {
      fields: { invoice_no: "INV-0042", date: "2026-09-18", total: "1284.00" },
      tables: { line_items: [{ description: "Stored", amount: "1284.00" }] },
      acknowledged: { revision: 1, issues: ["0:AI_UNCERTAIN:fields.total"] },
    },
  });
  const draft = createDraft(result);
  draft.fields.total = "1300.00";

  const next = cancelDraft(result);

  assert.equal(next.status, "reviewed");
  assert.deepEqual(next.review, result.review);
  assert.equal(next.draft.fields.total, "1284.00");
});

test("mark reviewed and rejected require valid canonical draft and acknowledgements", () => {
  const result = fixture();
  const draft = createDraft(result);
  const ack = acknowledged(result);

  assert.equal(actionState("needs_review", false, ["bad"], false).canReject, false);
  assert.equal(actionState("needs_review", false, [], true).canReject, true);
  assert.equal(markReviewed(result, draft, ack).status, "reviewed");
  assert.equal(markRejected(result, draft, ack).status, "rejected");
  assert.throws(() => markRejected(result, { ...draft, fields: { ...draft.fields, total: "1,284.00" } }, ack));
  assert.throws(() => markReviewed(result, draft, []));
});
