import {
  assignmentsForBox,
  evidenceForAssignments,
  fieldColor,
  groundSpan,
  quadToSvgPoints,
} from "./box-overlay-logic.mjs";

(function () {
  "use strict";

  const root = document.getElementById("boxOverlayRoot");
  if (!root) {
    return;
  }

  const initialState = JSON.parse(
    document.getElementById("boxOverlayData").textContent,
  );
  const { useMemo, useRef, useState } = React;

  function BoxOverlayApp() {
    const [currentPageIndex, setCurrentPageIndex] = useState(0);
    const [activeField, setActiveField] = useState(initialState.fields[0]?.key || "");
    const [assignments, setAssignments] = useState(initialState.assigned || {});
    const [spanValues, setSpanValues] = useState(initialState.spans || {});
    const [hoveredBox, setHoveredBox] = useState(null);
    const hoverTextRef = useRef(null);
    const page = initialState.snapshot.pages[currentPageIndex];
    const activeBoxIds = assignments[activeField] || [];
    const activeSpan = spanValues[activeField] || "";
    const spanGrounded = activeSpan
      ? groundSpan(initialState.snapshot, activeBoxIds, activeSpan)
      : null;
    const evidence = useMemo(
      () => evidenceForAssignments(assignments, spanValues, initialState.snapshot),
      [assignments, spanValues],
    );

    function setHoverTextContent(box) {
      setHoveredBox(box);
      if (hoverTextRef.current) {
        hoverTextRef.current.textContent = box
          ? `${box.id}: ${box.text || ""}`
          : "Hover a box to inspect its OCR text.";
      }
    }

    function toggleAssignment(box) {
      if (!activeField) {
        return;
      }
      setAssignments((current) => {
        const existing = current[activeField] || [];
        const nextFieldIds = existing.includes(box.id)
          ? existing.filter((boxId) => boxId !== box.id)
          : [...existing, box.id];
        return { ...current, [activeField]: nextFieldIds };
      });
      setSpanValues((current) => ({
        ...current,
        [activeField]: current[activeField] || box.text || "",
      }));
    }

    return html`
      <div className="row g-4">
        <div className="col-lg-8">
          <div className="d-flex align-items-center justify-content-between gap-3 mb-2">
            <div>
              <div className="fw-semibold">${initialState.document_label}</div>
              <div className="text-body-secondary small">
                ${page.label || `Page ${page.page}`}
              </div>
            </div>
            <div className="btn-group" role="group" aria-label="Page switcher">
              ${initialState.snapshot.pages.map(
                (candidatePage, index) => html`
                  <button
                    key=${candidatePage.page}
                    className=${index === currentPageIndex
                      ? "btn btn-primary btn-sm"
                      : "btn btn-outline-primary btn-sm"}
                    type="button"
                    onClick=${() => setCurrentPageIndex(index)}
                  >
                    ${candidatePage.page}
                  </button>
                `,
              )}
            </div>
          </div>

          <div className="position-relative border rounded overflow-hidden bg-body-tertiary">
            <img
              className="d-block w-100"
              src=${page.image}
              alt=${page.label || `Page ${page.page}`}
            />
            <svg
              className="position-absolute top-0 start-0 w-100 h-100"
              viewBox=${`0 0 ${page.width} ${page.height}`}
              preserveAspectRatio="none"
              role="img"
              aria-label="OCR boxes"
            >
              ${page.boxes.map((box) => {
                const assignedFields = assignmentsForBox(assignments, box.id);
                const assignedField = assignedFields[0];
                const highlighted = (initialState.result.highlighted_box_ids || []).includes(box.id);
                const selected = activeBoxIds.includes(box.id);
                const color = assignedField ? fieldColor(assignedField) : "#6c757d";
                return html`
                  <polygon
                    key=${box.id}
                    points=${quadToSvgPoints(box.quad, page)}
                    fill=${highlighted ? "#ffc107" : color}
                    fillOpacity=${assignedField || highlighted ? "0.28" : "0.08"}
                    stroke=${selected ? "#000000" : color}
                    strokeWidth=${selected ? "3" : "2"}
                    vectorEffect="non-scaling-stroke"
                    role="button"
                    tabIndex="0"
                    aria-label=${`${box.id} ${box.text || ""}`}
                    onMouseEnter=${() => setHoverTextContent(box)}
                    onMouseLeave=${() => setHoverTextContent(null)}
                    onFocus=${() => setHoverTextContent(box)}
                    onBlur=${() => setHoverTextContent(null)}
                    onClick=${() => toggleAssignment(box)}
                    onKeyDown=${(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        toggleAssignment(box);
                      }
                    }}
                  >
                    <title>${box.text || box.id}</title>
                  </polygon>
                `;
              })}
            </svg>
          </div>
          <p ref=${hoverTextRef} className="mt-2 mb-0 text-body-secondary" aria-live="polite">
            ${hoveredBox
              ? `${hoveredBox.id}: ${hoveredBox.text || ""}`
              : "Hover a box to inspect its OCR text."}
          </p>
        </div>

        <div className="col-lg-4">
          <div className="mb-3">
            <label className="form-label" htmlFor="activeField">Active field</label>
            <select
              id="activeField"
              className="form-select"
              value=${activeField}
              onChange=${(event) => setActiveField(event.target.value)}
            >
              ${initialState.fields.map(
                (field) => html`
                  <option key=${field.key} value=${field.key}>${field.label}</option>
                `,
              )}
            </select>
          </div>

          <div className="list-group mb-3">
            ${initialState.fields.map((field) => {
              const boxIds = assignments[field.key] || [];
              return html`
                <button
                  key=${field.key}
                  className=${field.key === activeField
                    ? "list-group-item list-group-item-action active"
                    : "list-group-item list-group-item-action"}
                  type="button"
                  onClick=${() => setActiveField(field.key)}
                >
                  <span className="d-flex align-items-center justify-content-between gap-2">
                    <span>${field.label}</span>
                    <span className="badge text-bg-light">${boxIds.length} assigned</span>
                  </span>
                </button>
              `;
            })}
          </div>

          <div className="mb-3">
            <label className="form-label" htmlFor="spanNarrowing">Span narrowing</label>
            <input
              id="spanNarrowing"
              className=${spanGrounded === false ? "form-control is-invalid" : "form-control"}
              value=${activeSpan}
              onInput=${(event) =>
                setSpanValues((current) => ({
                  ...current,
                  [activeField]: event.target.value,
                }))}
            />
            <div className=${spanGrounded === false ? "invalid-feedback d-block" : "form-text"}>
              ${spanGrounded === false
                ? "Span is not grounded in the assigned OCR boxes."
                : "Leave blank to use the full assigned box text."}
            </div>
          </div>

          <pre className="border rounded bg-body-tertiary p-3 small mb-0">${JSON.stringify(evidence, null, 2)}</pre>
        </div>
      </div>
    `;
  }

  ReactDOM.createRoot(root).render(html`<${BoxOverlayApp} />`);
})();
