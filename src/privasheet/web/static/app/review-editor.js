import {
  actionState,
  addTableRow,
  acknowledgementKey,
  allIssuesAcknowledged,
  buildDuplicateHref,
  buildReviewPayload,
  cancelDraft,
  createDraft,
  draftIssues,
  isDirty,
  markRejected,
  markReviewed,
  removeTableRow,
  saveDraft,
} from "./review-editor-logic.mjs";

(function () {
  "use strict";

  const root = document.getElementById("reviewEditorRoot");
  if (!root) {
    return;
  }

  const initialState = JSON.parse(
    document.getElementById("reviewEditorData").textContent,
  );
  const { useMemo, useState } = React;

  function inputType(item) {
    return item.type === "date" ? "date" : "text";
  }

  function issueText(issue) {
    if (issue.code === "AI_UNCERTAIN") {
      return "AI is uncertain. Refine the field prompt if this keeps happening.";
    }
    if (issue.code === "DUPLICATE_DOCUMENT") {
      return "Possible duplicate of an earlier document.";
    }
    return issue.message || issue.code;
  }

  function statusBadge(status) {
    const tone = {
      passed: "text-bg-success",
      needs_review: "text-bg-warning",
      reviewed: "text-bg-primary",
      rejected: "text-bg-danger",
      deleted: "text-bg-secondary",
    }[status] || "text-bg-secondary";
    return `badge ${tone}`;
  }

  function ReviewEditor() {
    const [result, setResult] = useState(initialState.result);
    const [draft, setDraft] = useState(() => createDraft(initialState.result));
    const [storedDraft, setStoredDraft] = useState(() => createDraft(initialState.result));
    const [editing, setEditing] = useState(initialState.result.status === "needs_review");
    const [acknowledgedIssues, setAcknowledgedIssues] = useState(
      initialState.result.review?.acknowledged?.issues || [],
    );
    const [message, setMessage] = useState("");
    const validationIssues = useMemo(() => draftIssues(result, draft), [result, draft]);
    const dirty = isDirty(draft, storedDraft);
    const acknowledged = allIssuesAcknowledged(result, acknowledgedIssues);
    const actions = actionState(result.status, dirty, validationIssues, acknowledged);

    function updateField(key, value) {
      setDraft((current) => ({
        ...current,
        fields: { ...current.fields, [key]: value },
      }));
    }

    function updateCell(tableKey, rowIndex, columnKey, value) {
      setDraft((current) => ({
        ...current,
        tables: {
          ...current.tables,
          [tableKey]: (current.tables[tableKey] || []).map((row, index) =>
            index === rowIndex ? { ...row, [columnKey]: value } : row,
          ),
        },
      }));
    }

    function toggleAcknowledgement(issue, index) {
      const key = acknowledgementKey(issue, index);
      setAcknowledgedIssues((current) =>
        current.includes(key)
          ? current.filter((candidate) => candidate !== key)
          : [...current, key],
      );
    }

    function persist(next) {
      setResult(next);
      const nextDraft = createDraft(next);
      setDraft(nextDraft);
      setStoredDraft(nextDraft);
      setAcknowledgedIssues(next.review?.acknowledged?.issues || []);
      setEditing(Boolean(next.editing));
    }

    function onSave() {
      try {
        persist(saveDraft(result, draft, acknowledgedIssues));
        setMessage("Saved for review.");
      } catch (error) {
        setMessage(error.message);
      }
    }

    function onCancel() {
      const cancelled = cancelDraft(result);
      setDraft(cancelled.draft);
      setAcknowledgedIssues(cancelled.acknowledgedIssues);
      setEditing(cancelled.editing);
      setMessage("Edits cancelled.");
    }

    function onMarkReviewed() {
      try {
        persist(markReviewed(result, draft, acknowledgedIssues));
        setEditing(false);
        setMessage("Marked reviewed.");
      } catch (error) {
        setMessage(error.message);
      }
    }

    function onMarkRejected() {
      try {
        persist(markRejected(result, draft, acknowledgedIssues));
        setEditing(false);
        setMessage("Marked rejected.");
      } catch (error) {
        setMessage(error.message);
      }
    }

    async function onDelete() {
      if (!(await window.confirm("Delete this review result?"))) {
        return;
      }
      setResult((current) => ({ ...current, status: "deleted" }));
      setEditing(false);
      setMessage("Deleted.");
    }

    const payload = buildReviewPayload(result, draft, acknowledgedIssues);

    return html`
      <div className="d-flex align-items-center justify-content-between gap-3 mb-3">
        <div>
          <div className="fw-semibold">${result.document_label || result.document_id}</div>
          <div className="text-body-secondary small">Revision ${result.revision}</div>
        </div>
        <span className=${statusBadge(result.status)}>${result.status}</span>
      </div>

      ${message &&
      html`<div className="alert alert-info py-2" role="status">${message}</div>`}

      <section className="mb-4">
        <h2 className="h5">Issues</h2>
        ${(result.issues || []).length === 0
          ? html`<p className="text-body-secondary mb-0">No issues.</p>`
          : html`
              <div className="list-group">
                ${(result.issues || []).map((issue, index) => {
                  const key = acknowledgementKey(issue, index);
                  const duplicateHref = buildDuplicateHref(issue);
                  return html`
                    <label key=${key} className="list-group-item">
                      <span className="d-flex align-items-start gap-2">
                        <input
                          className="form-check-input mt-1"
                          type="checkbox"
                          checked=${acknowledgedIssues.includes(key)}
                          disabled=${!editing}
                          onChange=${() => toggleAcknowledgement(issue, index)}
                        />
                        <span>
                          <span className="d-block fw-semibold">${issue.code}</span>
                          <span className="d-block">${issueText(issue)}</span>
                          ${duplicateHref &&
                          html`<a href=${duplicateHref}>Open earlier document</a>`}
                        </span>
                      </span>
                    </label>
                  `;
                })}
              </div>
            `}
      </section>

      <section className="mb-4">
        <h2 className="h5">Fields</h2>
        <div className="row g-3">
          ${(result.schema.fields || []).map((field) => html`
            <div key=${field.key} className="col-md-6">
              <label className="form-label" htmlFor=${`field-${field.key}`}>
                ${field.label || field.key}${field.required ? "" : " (optional)"}
              </label>
              <input
                id=${`field-${field.key}`}
                className="form-control"
                type=${inputType(field)}
                inputMode=${field.type === "decimal" ? "decimal" : undefined}
                value=${draft.fields[field.key] ?? ""}
                disabled=${!editing}
                onInput=${(event) => updateField(field.key, event.target.value)}
              />
            </div>
          `)}
        </div>
      </section>

      ${(result.schema.tables || []).map((table) => html`
        <section key=${table.key} className="mb-4">
          <div className="d-flex align-items-center justify-content-between gap-3 mb-2">
            <h2 className="h5 mb-0">${table.label || table.key}</h2>
            <button
              className="btn btn-outline-primary btn-sm"
              type="button"
              disabled=${!editing}
              onClick=${() => setDraft((current) => addTableRow(result, current, table.key))}
            >
              Add row
            </button>
          </div>
          <div className="table-responsive border rounded">
            <table className="table table-sm align-middle mb-0">
              <thead>
                <tr>
                  ${(table.columns || []).map((column) => html`
                    <th key=${column.key} scope="col">${column.label || column.key}</th>
                  `)}
                  <th scope="col" className="text-end">Rows</th>
                </tr>
              </thead>
              <tbody>
                ${(draft.tables[table.key] || []).map((row, rowIndex) => html`
                  <tr key=${rowIndex}>
                    ${(table.columns || []).map((column) => html`
                      <td key=${column.key}>
                        <input
                          className="form-control form-control-sm"
                          type=${inputType(column)}
                          inputMode=${column.type === "decimal" ? "decimal" : undefined}
                          aria-label=${`${column.label || column.key} row ${rowIndex + 1}`}
                          value=${row[column.key] ?? ""}
                          disabled=${!editing}
                          onInput=${(event) =>
                            updateCell(table.key, rowIndex, column.key, event.target.value)}
                        />
                      </td>
                    `)}
                    <td className="text-end">
                      <button
                        className="btn btn-outline-danger btn-sm"
                        type="button"
                        disabled=${!editing}
                        onClick=${() => setDraft((current) => removeTableRow(current, table.key, rowIndex))}
                      >
                        Remove
                      </button>
                    </td>
                  </tr>
                `)}
              </tbody>
            </table>
          </div>
        </section>
      `)}

      ${validationIssues.length > 0 &&
      html`
        <div className="alert alert-danger">
          <div className="fw-semibold">Fix canonical values before saving.</div>
          <ul className="mb-0">
            ${validationIssues.map((issue) => html`
              <li key=${issue.path}>${issue.message}</li>
            `)}
          </ul>
        </div>
      `}

      <div className="d-flex flex-wrap align-items-center gap-2 mb-4">
        ${!editing &&
        html`
          <button className="btn btn-outline-primary" type="button" disabled=${!actions.canEdit} onClick=${() => setEditing(true)}>
            Edit
          </button>
        `}
        ${editing &&
        html`
          <button className="btn btn-primary" type="button" disabled=${!actions.canSave} onClick=${onSave}>
            Save
          </button>
          <button className="btn btn-outline-secondary" type="button" disabled=${!actions.canCancel} onClick=${onCancel}>
            Cancel
          </button>
        `}
        <button className="btn btn-success" type="button" disabled=${!actions.canMarkReviewed} onClick=${onMarkReviewed}>
          Mark reviewed
        </button>
        <button className="btn btn-outline-danger" type="button" disabled=${!actions.canReject} onClick=${onMarkRejected}>
          Mark rejected
        </button>
        <button className="btn btn-danger ms-auto" type="button" disabled=${!actions.canDelete} onClick=${onDelete}>
          Delete
        </button>
      </div>

      <details>
        <summary>Review JSON</summary>
        <pre className="border rounded bg-body-tertiary p-3 small mt-2">${JSON.stringify(payload, null, 2)}</pre>
      </details>
    `;
  }

  ReactDOM.createRoot(root).render(html`<${ReviewEditor} />`);
})();
