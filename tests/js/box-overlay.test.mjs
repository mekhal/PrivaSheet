import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  assignmentsForBox,
  fieldColor,
  groundSpan,
  quadToSvgPoints,
} from "../../src/privasheet/web/static/app/box-overlay-logic.mjs";

const snapshot = {
  pages: [
    {
      page: 1,
      width: 200,
      height: 100,
      boxes: [
        {
          id: "p1-b1",
          text: "Invoice   No: INV-0042",
          quad: [
            [0.1, 0.2],
            [0.4, 0.2],
            [0.4, 0.3],
            [0.1, 0.3],
          ],
        },
        { id: "p1-b2", text: "Grand\nTotal 1,284.00" },
      ],
    },
  ],
};

test("quadToSvgPoints maps normalized quads to page coordinates", () => {
  assert.equal(
    quadToSvgPoints(snapshot.pages[0].boxes[0].quad, snapshot.pages[0]),
    "20,20 80,20 80,30 20,30",
  );
});

test("fieldColor is deterministic and stable for known fields", () => {
  assert.equal(fieldColor("invoice_no"), "#0d6efd");
  assert.equal(fieldColor("total"), "#198754");
  assert.equal(fieldColor("invoice_no"), fieldColor("invoice_no"));
});

test("assignmentsForBox returns all fields assigned to a box", () => {
  assert.deepEqual(
    assignmentsForBox({ invoice_no: ["p1-b1"], total: ["p1-b1", "p1-b2"] }, "p1-b1"),
    ["invoice_no", "total"],
  );
});

test("groundSpan uses collapsed case-sensitive text across selected boxes", () => {
  assert.equal(groundSpan(snapshot, ["p1-b1", "p1-b2"], "INV-0042 Grand Total"), true);
  assert.equal(groundSpan(snapshot, ["p1-b1"], "invoice no"), false);
  assert.equal(groundSpan(snapshot, ["missing"], "INV-0042"), false);
  assert.equal(groundSpan(snapshot, ["p1-b1"], "   "), false);
});

test("react entry wires page switching, text-safe hover, and click assignment", () => {
  const source = readFileSync(
    new URL(
      "../../src/privasheet/web/static/app/box-overlay.js",
      import.meta.url,
    ),
    "utf8",
  );

  assert.match(source, /boxOverlayRoot/);
  assert.match(source, /setCurrentPageIndex/);
  assert.match(source, /setHoveredBox/);
  assert.match(source, /setAssignments/);
  assert.match(source, /textContent/);
  assert.doesNotMatch(source, /dangerouslySetInnerHTML/);
});
