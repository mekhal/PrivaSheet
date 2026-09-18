import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
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
    format: "DD/MM/YYYY",
    description: "The invoice issue date.",
  },
  {
    tag: "#line_items",
    key: "line_items",
    type: "table",
    kind: "table",
    description: "One row per purchased item.",
    columns: [
      { key: "description", type: "text" },
      { key: "service_date", type: "date", format: "DD MMM YY" },
    ],
  },
  {
    tag: "#required",
    kind: "flag",
  },
];

test("suggestions filter hashtag presets by typed prefix", () => {
  assert.deepEqual(
    filterPresetSuggestions("#invoice", presets).map((preset) => preset.tag),
    ["#invoice_date"],
  );
  assert.deepEqual(
    filterPresetSuggestions("line", presets).map((preset) => preset.tag),
    ["#line_items"],
  );
});

test("applying a preset fills editable field state and defaults date format", () => {
  assert.deepEqual(applyPreset({ required: false }, presets[0]), {
    key: "invoice_date",
    type: "date",
    required: false,
    description: "The invoice issue date.",
    format: "DD/MM/YYYY",
  });
});

test("template draft includes chosen date formats for fields and table columns", () => {
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
        required: true,
        description: "One row per purchased item.",
        columns: [
          { key: "description", type: "text", required: true },
          {
            key: "service_date",
            type: "date",
            required: false,
            format: "DD MMM YY",
          },
        ],
      },
    ],
  });

  assert.equal(draft.fields[0].format, "YYYY-MM-DD");
  assert.equal(draft.tables[0].columns[1].format, "DD MMM YY");
});

test("react entry wires the field editor controls", () => {
  const source = readFileSync(
    new URL(
      "../../src/privasheet/web/static/app/field-editor.js",
      import.meta.url,
    ),
    "utf8",
  );

  assert.match(source, /FieldEditor/);
  assert.match(source, /filterPresetSuggestions/);
  assert.match(source, /format/);
  assert.match(source, /ReactDOM\.createRoot/);
});
