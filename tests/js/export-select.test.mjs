import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  isSelectableDocument,
  selectAllDocuments,
  selectedCountLabel,
  toggleDocumentSelection,
} from "../../src/privasheet/web/static/app/export-select-logic.mjs";

const manifest = {
  documents: [
    { document_id: "doc_passed", result_id: "res_passed" },
    { document_id: "doc_reviewed", result_id: "res_reviewed" },
    { document_id: "doc_failed", result_id: "res_failed" },
    { document_id: "doc_processing", result_id: "res_processing" },
    { document_id: "doc_missing", result_id: "res_missing" },
  ],
};
const results = {
  res_passed: { status: "passed" },
  res_reviewed: { status: "reviewed" },
  res_failed: { status: "failed" },
  res_processing: { status: "processing" },
};

test("selection rule allows only passed and reviewed documents", () => {
  assert.equal(isSelectableDocument(manifest.documents[0], results), true);
  assert.equal(isSelectableDocument(manifest.documents[1], results), true);
  assert.equal(isSelectableDocument(manifest.documents[2], results), false);
  assert.equal(isSelectableDocument(manifest.documents[4], results), false);
});

test("select all returns only exportable document ids in manifest order", () => {
  assert.deepEqual(selectAllDocuments(manifest, results), [
    "doc_passed",
    "doc_reviewed",
  ]);
});

test("toggle ignores disabled documents and removes selected ids", () => {
  assert.deepEqual(toggleDocumentSelection([], "doc_passed", manifest, results), [
    "doc_passed",
  ]);
  assert.deepEqual(
    toggleDocumentSelection(["doc_passed"], "doc_passed", manifest, results),
    [],
  );
  assert.deepEqual(
    toggleDocumentSelection(["doc_passed"], "doc_failed", manifest, results),
    ["doc_passed"],
  );
});

test("selected count label is human readable", () => {
  assert.equal(selectedCountLabel([]), "0 selected");
  assert.equal(selectedCountLabel(["doc_passed"]), "1 selected");
  assert.equal(selectedCountLabel(["doc_passed", "doc_reviewed"]), "2 selected");
});

test("react entry wires the expected controls", () => {
  const source = readFileSync(
    new URL(
      "../../src/privasheet/web/static/app/export-select.js",
      import.meta.url,
    ),
    "utf8",
  );

  assert.match(source, /selectAllDocuments/);
  assert.match(source, /toggleDocumentSelection/);
  assert.match(source, /Download/);
  assert.match(source, /ReactDOM\.createRoot/);
});
