import {
  documentStatus,
  isSelectableDocument,
  selectAllDocuments,
  selectedCountLabel,
  toggleDocumentSelection,
} from "./export-select-logic.mjs";

(function () {
  "use strict";

  const root = document.getElementById("exportSelectRoot");
  if (!root) {
    return;
  }

  const initialState = JSON.parse(
    document.getElementById("exportSelectData").textContent,
  );
  const { manifest, results, action } = initialState;
  const { useState } = React;

  function downloadBlob(blob, filename) {
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  }

  function filenameFromDisposition(value) {
    const match = /filename="([^"]+)"/.exec(value || "");
    return match ? match[1] : "export.jsonl";
  }

  function ExportSelect() {
    const [selectedIds, setSelectedIds] = useState([]);
    const [error, setError] = useState("");
    const [busy, setBusy] = useState(false);
    const allSelectableIds = selectAllDocuments(manifest, results);
    const allSelected =
      allSelectableIds.length > 0 &&
      allSelectableIds.every((documentId) => selectedIds.includes(documentId));

    function onSelectAll(event) {
      setSelectedIds(event.target.checked ? allSelectableIds : []);
    }

    function onToggle(documentId) {
      setSelectedIds((current) =>
        toggleDocumentSelection(current, documentId, manifest, results),
      );
    }

    async function onSubmit(event) {
      event.preventDefault();
      if (selectedIds.length === 0 || busy) {
        return;
      }

      setBusy(true);
      setError("");
      try {
        const response = await fetch(action, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ selected_document_ids: selectedIds }),
        });
        if (!response.ok) {
          throw new Error(await response.text());
        }
        const blob = await response.blob();
        downloadBlob(blob, filenameFromDisposition(response.headers.get("content-disposition")));
      } catch (downloadError) {
        setError(downloadError.message || "Export failed");
      } finally {
        setBusy(false);
      }
    }

    return html`
      <form onSubmit=${onSubmit}>
        <div className="d-flex align-items-center justify-content-between gap-3 mb-3">
          <div className="form-check">
            <input
              id="selectAllDocuments"
              className="form-check-input"
              type="checkbox"
              checked=${allSelected}
              disabled=${allSelectableIds.length === 0}
              onChange=${onSelectAll}
            />
            <label className="form-check-label" htmlFor="selectAllDocuments">
              Select all
            </label>
          </div>
          <div className="d-flex align-items-center gap-3">
            <span className="text-body-secondary" aria-live="polite">
              ${selectedCountLabel(selectedIds)}
            </span>
            <button
              className="btn btn-primary"
              type="submit"
              disabled=${selectedIds.length === 0 || busy}
            >
              ${busy ? "Preparing..." : "Download"}
            </button>
          </div>
        </div>

        <div className="table-responsive border rounded">
          <table className="table table-hover align-middle mb-0">
            <thead>
              <tr>
                <th scope="col" className="text-center">Export</th>
                <th scope="col">Document</th>
                <th scope="col">Source file</th>
                <th scope="col">Status</th>
              </tr>
            </thead>
            <tbody>
              ${manifest.documents.map((document) => {
                const status = documentStatus(document, results);
                const selectable = isSelectableDocument(document, results);
                const checked = selectedIds.includes(document.document_id);
                return html`
                  <tr key=${document.document_id} className=${selectable ? "" : "table-light"}>
                    <td className="text-center">
                      <input
                        className="form-check-input"
                        type="checkbox"
                        aria-label=${`Select ${document.source_file}`}
                        checked=${checked}
                        disabled=${!selectable}
                        onChange=${() => onToggle(document.document_id)}
                      />
                    </td>
                    <td><code>${document.document_id}</code></td>
                    <td>${document.source_file}</td>
                    <td>
                      <span className=${selectable ? "badge text-bg-success" : "badge text-bg-secondary"}>
                        ${status}
                      </span>
                    </td>
                  </tr>
                `;
              })}
            </tbody>
          </table>
        </div>
        ${error &&
        html`<div className="alert alert-danger mt-3" role="alert">${error}</div>`}
      </form>
    `;
  }

  ReactDOM.createRoot(root).render(html`<${ExportSelect} />`);
})();
