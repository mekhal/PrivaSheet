import assert from "node:assert/strict";
import test from "node:test";

import {
  applyPreset,
  buildTemplateDraft,
  filterPresetSuggestions,
} from "../../src/privasheet/web/static/app/field-editor-logic.mjs";

const presets = [
  {
    tag: "#invoice_date",
    key: "invoice_date",
    type: "date",
    kind: "field",
    description: "The invoice issue date.",
  },
  {
    tag: "#line_items",
    key: "line_items",
    type: "table",
    kind: "table",
    columns: [{ key: "service_date", type: "date", format: "DD MMM YY" }],
  },
  { tag: "#required", kind: "flag" },
];

test("suggestions filter hashtag presets by typed prefix", () => {
  assert.deepEqual(
    filterPresetSuggestions("invoice", presets).map((preset) => preset.tag),
    ["#invoice_date"],
  );
});

test("applying presets fills editable state", () => {
  assert.deepEqual(applyPreset({ required: false }, presets[0]), {
    key: "invoice_date",
    type: "date",
    required: false,
    description: "The invoice issue date.",
    format: "DD/MM/YYYY",
  });
  assert.equal(applyPreset({ required: true }, presets[2]).required, false);
});

test("template draft includes chosen field and column date formats", () => {
  const draft = buildTemplateDraft({
    fields: [
      {
        key: "invoice_date",
        type: "date",
        required: true,
        key_label: true,
        description: "Invoice date.",
        format: "YYYY-MM-DD",
      },
    ],
    tables: [
      {
        key: "line_items",
        columns: [
          { key: "service_date", type: "date", required: false, format: "DD MMM YY" },
        ],
      },
    ],
  });

  assert.equal(draft.fields[0].format, "YYYY-MM-DD");
  assert.equal(draft.tables[0].columns[0].format, "DD MMM YY");
});
