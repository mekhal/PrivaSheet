import {
  DEFAULT_DATE_FORMAT,
  FIELD_TYPES,
  applyPreset,
  blankColumn,
  blankField,
  blankTable,
  buildTemplateDraft,
  filterPresetSuggestions,
  moveItem,
  validationErrorsByPath,
} from "./field-editor-logic.mjs";

(function () {
  "use strict";

  const { useEffect, useMemo, useState } = React;
  const html = window.html;
  const root = document.getElementById("fieldEditorRoot");
  if (!root) {
    return;
  }

  function pathErrors(groups, path) {
    return groups[path] || [];
  }

  function ErrorList({ errors }) {
    if (!errors.length) {
      return null;
    }
    return html`<div className="invalid-feedback d-block">${errors.join(" ")}</div>`;
  }

  function SuggestionBox({ suggestions, activeIndex, onPick }) {
    if (!suggestions.length) {
      return null;
    }
    return html`
      <div className="list-group position-absolute z-3 w-100 shadow-sm">
        ${suggestions.map(
          (preset, index) => html`
            <button
              key=${preset.tag}
              type="button"
              className=${`list-group-item list-group-item-action ${
                index === activeIndex ? "active" : ""
              }`}
              onMouseDown=${(event) => {
                event.preventDefault();
                onPick(preset);
              }}
            >
              <span className="fw-semibold">${preset.tag}</span>
              ${preset.kind === "table"
                ? html`<span className="badge text-bg-secondary ms-2">table</span>`
                : null}
              <span className="d-block small">${preset.description || "Toggle required"}</span>
            </button>
          `,
        )}
      </div>
    `;
  }

  function HashtagInput({ label, value, presets, onChange, onPick }) {
    const [activeIndex, setActiveIndex] = useState(0);
    const suggestions = useMemo(
      () => filterPresetSuggestions(value, presets),
      [value, presets],
    );

    useEffect(() => setActiveIndex(0), [value]);

    return html`
      <div className="position-relative">
        <label className="form-label">${label}</label>
        <input
          className="form-control"
          value=${value}
          placeholder="#invoice_date"
          onInput=${(event) => onChange(event.target.value)}
          onKeyDown=${(event) => {
            if (!suggestions.length) {
              return;
            }
            if (event.key === "ArrowDown") {
              event.preventDefault();
              setActiveIndex((activeIndex + 1) % suggestions.length);
            } else if (event.key === "ArrowUp") {
              event.preventDefault();
              setActiveIndex(
                (activeIndex - 1 + suggestions.length) % suggestions.length,
              );
            } else if (event.key === "Enter") {
              event.preventDefault();
              onPick(suggestions[activeIndex]);
            }
          }}
        />
        <${SuggestionBox}
          suggestions=${suggestions}
          activeIndex=${activeIndex}
          onPick=${onPick}
        />
      </div>
    `;
  }

  function RequiredToggle({ checked, onChange }) {
    return html`
      <label className="form-check form-switch">
        <input
          className="form-check-input"
          type="checkbox"
          checked=${checked}
          onChange=${(event) => onChange(event.target.checked)}
        />
        <span className="form-check-label">Required</span>
      </label>
    `;
  }

  function TypeSelect({ value, onChange }) {
    return html`
      <select
        className="form-select"
        value=${value}
        onChange=${(event) => onChange(event.target.value)}
      >
        ${FIELD_TYPES.map(
          (type) => html`<option key=${type} value=${type}>${type}</option>`,
        )}
      </select>
    `;
  }

  function FieldForm({ field, presets, hashtag, onHashtag, onChange, onAdd }) {
    function update(patch) {
      onChange({ ...field, ...patch });
    }

    return html`
      <section className="border rounded p-3">
        <h2 className="h5">Field</h2>
        <div className="row g-3 align-items-end">
          <div className="col-md-4">
            <${HashtagInput}
              label="Hashtag"
              value=${hashtag}
              presets=${presets}
              onChange=${onHashtag}
              onPick=${(preset) => {
                update(applyPreset(field, preset));
                onHashtag(preset.tag);
              }}
            />
          </div>
          <div className="col-md-3">
            <label className="form-label">Key</label>
            <input
              className="form-control"
              value=${field.key}
              onInput=${(event) => update({ key: event.target.value })}
            />
          </div>
          <div className="col-md-2">
            <label className="form-label">Type</label>
            <${TypeSelect}
              value=${field.type}
              onChange=${(type) =>
                update({
                  type,
                  format: type === "date" ? field.format || DEFAULT_DATE_FORMAT : field.format,
                })}
            />
          </div>
          ${field.type === "date"
            ? html`
                <div className="col-md-3">
                  <label className="form-label">Date format</label>
                  <input
                    className="form-control"
                    value=${field.format || DEFAULT_DATE_FORMAT}
                    onInput=${(event) => update({ format: event.target.value })}
                  />
                </div>
              `
            : null}
          <div className="col-md-8">
            <label className="form-label">Description</label>
            <input
              className="form-control"
              value=${field.description}
              onInput=${(event) => update({ description: event.target.value })}
            />
          </div>
          <div className="col-md-2">
            <label className="form-check">
              <input
                className="form-check-input"
                type="checkbox"
                checked=${field.key_label === true}
                onChange=${(event) => update({ key_label: event.target.checked })}
              />
              <span className="form-check-label">Key label</span>
            </label>
          </div>
          <div className="col-md-2">
            <${RequiredToggle}
              checked=${field.required}
              onChange=${(required) => update({ required })}
            />
          </div>
          <div className="col-12">
            <button className="btn btn-primary" type="button" onClick=${onAdd}>
              Add field
            </button>
          </div>
        </div>
      </section>
    `;
  }

  function ColumnEditor({ column, path, errorGroups, onChange, onRemove }) {
    function update(patch) {
      onChange({ ...column, ...patch });
    }

    return html`
      <div className="row g-2 align-items-start">
        <div className="col-md-4">
          <label className="form-label">Column key</label>
          <input
            className="form-control"
            value=${column.key}
            onInput=${(event) => update({ key: event.target.value })}
          />
          <${ErrorList} errors=${pathErrors(errorGroups, `${path}.key`)} />
        </div>
        <div className="col-md-2">
          <label className="form-label">Type</label>
          <${TypeSelect}
            value=${column.type}
            onChange=${(type) =>
              update({
                type,
                format: type === "date" ? column.format || DEFAULT_DATE_FORMAT : column.format,
              })}
          />
          <${ErrorList} errors=${pathErrors(errorGroups, `${path}.type`)} />
        </div>
        ${column.type === "date"
          ? html`
              <div className="col-md-3">
                <label className="form-label">Date format</label>
                <input
                  className="form-control"
                  value=${column.format || DEFAULT_DATE_FORMAT}
                  onInput=${(event) => update({ format: event.target.value })}
                />
                <${ErrorList} errors=${pathErrors(errorGroups, `${path}.format`)} />
              </div>
            `
          : null}
        <div className="col-md-2 pt-md-4">
          <${RequiredToggle}
            checked=${column.required}
            onChange=${(required) => update({ required })}
          />
        </div>
        <div className="col-md-1 pt-md-4 text-end">
          <button
            className="btn btn-outline-danger btn-sm"
            type="button"
            aria-label="Remove column"
            onClick=${onRemove}
          >
            Remove
          </button>
        </div>
      </div>
    `;
  }

  function TableForm({ table, presets, hashtag, onHashtag, onChange, onAdd }) {
    function update(patch) {
      onChange({ ...table, ...patch });
    }

    function updateColumn(index, column) {
      const columns = table.columns.slice();
      columns[index] = column;
      update({ columns });
    }

    return html`
      <section className="border rounded p-3">
        <h2 className="h5">Table</h2>
        <div className="row g-3 align-items-end">
          <div className="col-md-4">
            <${HashtagInput}
              label="Hashtag"
              value=${hashtag}
              presets=${presets.filter(
                (preset) => preset.kind === "table" || preset.tag === "#required",
              )}
              onChange=${onHashtag}
              onPick=${(preset) => {
                onChange(applyPreset(table, preset));
                onHashtag(preset.tag);
              }}
            />
          </div>
          <div className="col-md-4">
            <label className="form-label">Key</label>
            <input
              className="form-control"
              value=${table.key}
              onInput=${(event) => update({ key: event.target.value })}
            />
          </div>
          <div className="col-md-2">
            <${RequiredToggle}
              checked=${table.required}
              onChange=${(required) => update({ required })}
            />
          </div>
          <div className="col-12">
            <label className="form-label">Description</label>
            <input
              className="form-control"
              value=${table.description}
              onInput=${(event) => update({ description: event.target.value })}
            />
          </div>
        </div>
        <div className="vstack gap-2 mt-3">
          ${table.columns.map(
            (column, index) => html`
              <${ColumnEditor}
                key=${index}
                column=${column}
                path=${`tables[0].columns[${index}]`}
                errorGroups=${{}}
                onChange=${(next) => updateColumn(index, next)}
                onRemove=${() =>
                  update({
                    columns: table.columns.filter((_, current) => current !== index),
                  })}
              />
            `,
          )}
        </div>
        <div className="d-flex gap-2 mt-3">
          <button
            className="btn btn-outline-secondary"
            type="button"
            onClick=${() => update({ columns: [...table.columns, blankColumn()] })}
          >
            Add column
          </button>
          <button className="btn btn-primary" type="button" onClick=${onAdd}>
            Add table
          </button>
        </div>
      </section>
    `;
  }

  function DraftList({ state, setState, errorGroups }) {
    function updateField(index, patch) {
      setState((state) => ({
        ...state,
        fields: state.fields.map((field, current) =>
          current === index ? { ...field, ...patch } : field,
        ),
      }));
    }

    function updateTable(index, table) {
      setState((state) => ({
        ...state,
        tables: state.tables.map((currentTable, current) =>
          current === index ? table : currentTable,
        ),
      }));
    }

    return html`
      <section>
        <h2 className="h5">Draft</h2>
        <div className="vstack gap-3">
          ${state.fields.map(
            (field, index) => html`
              <div key=${`field-${index}`} className="border rounded p-3">
                <div className="d-flex justify-content-between gap-2 mb-2">
                  <strong>${field.key || "Untitled field"}</strong>
                  <div className="btn-group btn-group-sm">
                    <button
                      className="btn btn-outline-secondary"
                      type="button"
                      onClick=${() =>
                        setState((state) => ({
                          ...state,
                          fields: moveItem(state.fields, index, -1),
                        }))}
                    >
                      Up
                    </button>
                    <button
                      className="btn btn-outline-secondary"
                      type="button"
                      onClick=${() =>
                        setState((state) => ({
                          ...state,
                          fields: moveItem(state.fields, index, 1),
                        }))}
                    >
                      Down
                    </button>
                    <button
                      className="btn btn-outline-danger"
                      type="button"
                      onClick=${() =>
                        setState((state) => ({
                          ...state,
                          fields: state.fields.filter((_, current) => current !== index),
                        }))}
                    >
                      Remove
                    </button>
                  </div>
                </div>
                <div className="row g-2">
                  <div className="col-md-3">
                    <label className="form-label">Key</label>
                    <input
                      className="form-control"
                      value=${field.key}
                      onInput=${(event) => updateField(index, { key: event.target.value })}
                    />
                    <${ErrorList} errors=${pathErrors(errorGroups, `fields[${index}].key`)} />
                  </div>
                  <div className="col-md-2">
                    <label className="form-label">Type</label>
                    <${TypeSelect}
                      value=${field.type}
                      onChange=${(type) =>
                        updateField(index, {
                          type,
                          format: type === "date" ? field.format || DEFAULT_DATE_FORMAT : field.format,
                        })}
                    />
                    <${ErrorList} errors=${pathErrors(errorGroups, `fields[${index}].type`)} />
                  </div>
                  ${field.type === "date"
                    ? html`
                        <div className="col-md-3">
                          <label className="form-label">Date format</label>
                          <input
                            className="form-control"
                            value=${field.format || DEFAULT_DATE_FORMAT}
                            onInput=${(event) =>
                              updateField(index, { format: event.target.value })}
                          />
                          <${ErrorList}
                            errors=${pathErrors(errorGroups, `fields[${index}].format`)}
                          />
                        </div>
                      `
                    : null}
                  <div className="col-md-2 pt-md-4">
                    <${RequiredToggle}
                      checked=${field.required}
                      onChange=${(required) => updateField(index, { required })}
                    />
                  </div>
                  <div className="col-md-2 pt-md-4">
                    <label className="form-check">
                      <input
                        className="form-check-input"
                        type="checkbox"
                        checked=${field.key_label === true}
                        onChange=${(event) =>
                          updateField(index, { key_label: event.target.checked })}
                      />
                      <span className="form-check-label">Key label</span>
                    </label>
                  </div>
                  <div className="col-12">
                    <label className="form-label">Description</label>
                    <input
                      className="form-control"
                      value=${field.description}
                      onInput=${(event) =>
                        updateField(index, { description: event.target.value })}
                    />
                    <${ErrorList}
                      errors=${pathErrors(errorGroups, `fields[${index}].description`)}
                    />
                  </div>
                </div>
              </div>
            `,
          )}
          ${state.tables.map(
            (table, tableIndex) => html`
              <div key=${`table-${tableIndex}`} className="border rounded p-3">
                <div className="d-flex justify-content-between gap-2 mb-2">
                  <strong>${table.key || "Untitled table"}</strong>
                  <div className="btn-group btn-group-sm">
                    <button
                      className="btn btn-outline-secondary"
                      type="button"
                      onClick=${() =>
                        setState((state) => ({
                          ...state,
                          tables: moveItem(state.tables, tableIndex, -1),
                        }))}
                    >
                      Up
                    </button>
                    <button
                      className="btn btn-outline-secondary"
                      type="button"
                      onClick=${() =>
                        setState((state) => ({
                          ...state,
                          tables: moveItem(state.tables, tableIndex, 1),
                        }))}
                    >
                      Down
                    </button>
                    <button
                      className="btn btn-outline-danger"
                      type="button"
                      onClick=${() =>
                        setState((state) => ({
                          ...state,
                          tables: state.tables.filter((_, current) => current !== tableIndex),
                        }))}
                    >
                      Remove
                    </button>
                  </div>
                </div>
                <div className="row g-2 mb-3">
                  <div className="col-md-4">
                    <label className="form-label">Key</label>
                    <input
                      className="form-control"
                      value=${table.key}
                      onInput=${(event) =>
                        updateTable(tableIndex, { ...table, key: event.target.value })}
                    />
                    <${ErrorList}
                      errors=${pathErrors(errorGroups, `tables[${tableIndex}].key`)}
                    />
                  </div>
                  <div className="col-md-3 pt-md-4">
                    <${RequiredToggle}
                      checked=${table.required}
                      onChange=${(required) =>
                        updateTable(tableIndex, { ...table, required })}
                    />
                  </div>
                  <div className="col-12">
                    <label className="form-label">Description</label>
                    <input
                      className="form-control"
                      value=${table.description}
                      onInput=${(event) =>
                        updateTable(tableIndex, {
                          ...table,
                          description: event.target.value,
                        })}
                    />
                    <${ErrorList}
                      errors=${pathErrors(
                        errorGroups,
                        `tables[${tableIndex}].description`,
                      )}
                    />
                  </div>
                </div>
                <div className="vstack gap-2">
                  ${table.columns.map(
                    (column, columnIndex) => html`
                      <${ColumnEditor}
                        key=${columnIndex}
                        column=${column}
                        path=${`tables[${tableIndex}].columns[${columnIndex}]`}
                        errorGroups=${errorGroups}
                        onChange=${(nextColumn) => {
                          const columns = table.columns.slice();
                          columns[columnIndex] = nextColumn;
                          updateTable(tableIndex, { ...table, columns });
                        }}
                        onRemove=${() =>
                          updateTable(tableIndex, {
                            ...table,
                            columns: table.columns.filter(
                              (_, current) => current !== columnIndex,
                            ),
                          })}
                      />
                    `,
                  )}
                </div>
                <button
                  className="btn btn-outline-secondary btn-sm mt-3"
                  type="button"
                  onClick=${() =>
                    updateTable(tableIndex, {
                      ...table,
                      columns: [...table.columns, blankColumn()],
                    })}
                >
                  Add column
                </button>
              </div>
            `,
          )}
        </div>
      </section>
    `;
  }

  function FieldEditor() {
    const [presets, setPresets] = useState([]);
    const [field, setField] = useState(blankField());
    const [table, setTable] = useState(blankTable());
    const [fieldHashtag, setFieldHashtag] = useState("");
    const [tableHashtag, setTableHashtag] = useState("");
    const [state, setState] = useState({ fields: [], tables: [] });
    const [errors, setErrors] = useState([]);

    useEffect(() => {
      fetch("/api/presets")
        .then((response) => response.json())
        .then((payload) => setPresets(payload.presets || []));
    }, []);

    const draft = useMemo(() => buildTemplateDraft(state), [state]);
    const errorGroups = useMemo(() => validationErrorsByPath(errors), [errors]);

    useEffect(() => {
      fetch("/api/templates/validate", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(draft),
      })
        .then((response) => response.json())
        .then((payload) => setErrors(payload.errors || []));
    }, [draft]);

    return html`
      <div className="vstack gap-4">
        <div className="row g-4">
          <div className="col-lg-6">
            <${FieldForm}
              field=${field}
              presets=${presets.filter((preset) => preset.kind !== "table")}
              hashtag=${fieldHashtag}
              onHashtag=${setFieldHashtag}
              onChange=${setField}
              onAdd=${() => {
                setState((state) => ({ ...state, fields: [...state.fields, field] }));
                setField(blankField());
                setFieldHashtag("");
              }}
            />
          </div>
          <div className="col-lg-6">
            <${TableForm}
              table=${table}
              presets=${presets}
              hashtag=${tableHashtag}
              onHashtag=${setTableHashtag}
              onChange=${setTable}
              onAdd=${() => {
                setState((state) => ({ ...state, tables: [...state.tables, table] }));
                setTable(blankTable());
                setTableHashtag("");
              }}
            />
          </div>
        </div>
        <${DraftList} state=${state} setState=${setState} errorGroups=${errorGroups} />
        <div>
          <label className="form-label">Template draft</label>
          <pre className="border rounded p-3 bg-body-tertiary"><code>${JSON.stringify(
            draft,
            null,
            2,
          )}</code></pre>
        </div>
      </div>
    `;
  }

  ReactDOM.createRoot(root).render(html`<${FieldEditor} />`);
})();
