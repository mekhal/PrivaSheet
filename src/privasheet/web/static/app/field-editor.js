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
  if (!root) return;

  const setAt = (items, index, value) =>
    items.map((item, current) => (current === index ? value : item));
  const removeAt = (items, index) => items.filter((_, current) => current !== index);
  const err = (groups, path) =>
    groups[path]?.length
      ? html`<div className="invalid-feedback d-block">${groups[path].join(" ")}</div>`
      : null;

  function HashtagInput({ label, value, presets, onChange, onPick }) {
    const [active, setActive] = useState(0);
    const suggestions = useMemo(
      () => filterPresetSuggestions(value, presets),
      [value, presets],
    );
    useEffect(() => setActive(0), [value]);
    const choose = (preset) => {
      onPick(preset);
      onChange(preset.tag);
    };

    return html`
      <div className="position-relative">
        <label className="form-label">${label}</label>
        <input
          className="form-control"
          value=${value}
          placeholder="#invoice_date"
          onInput=${(event) => onChange(event.target.value)}
          onKeyDown=${(event) => {
            if (!suggestions.length) return;
            if (["ArrowDown", "ArrowUp"].includes(event.key)) {
              event.preventDefault();
              setActive(
                (active + (event.key === "ArrowDown" ? 1 : -1) + suggestions.length) %
                  suggestions.length,
              );
            }
            if (event.key === "Enter") {
              event.preventDefault();
              choose(suggestions[active]);
            }
          }}
        />
        ${suggestions.length
          ? html`<div className="list-group position-absolute z-3 w-100 shadow-sm">
              ${suggestions.map(
                (preset, index) => html`
                  <button
                    key=${preset.tag}
                    type="button"
                    className=${`list-group-item list-group-item-action ${
                      index === active ? "active" : ""
                    }`}
                    onMouseDown=${(event) => {
                      event.preventDefault();
                      choose(preset);
                    }}
                  >
                    <span className="fw-semibold">${preset.tag}</span>
                    ${preset.kind === "table"
                      ? html`<span className="badge text-bg-secondary ms-2">table</span>`
                      : null}
                    <span className="d-block small">
                      ${preset.description || "Toggle required"}
                    </span>
                  </button>
                `,
              )}
            </div>`
          : null}
      </div>
    `;
  }

  function TypeSelect({ value, onChange }) {
    return html`<select
      className="form-select"
      value=${value}
      onChange=${(event) => onChange(event.target.value)}
    >
      ${FIELD_TYPES.map((type) => html`<option key=${type} value=${type}>${type}</option>`)}
    </select>`;
  }

  function Required({ checked, onChange }) {
    return html`<label className="form-check form-switch mb-0">
      <input
        className="form-check-input"
        type="checkbox"
        checked=${checked}
        onChange=${(event) => onChange(event.target.checked)}
      />
      <span className="form-check-label">Required</span>
    </label>`;
  }

  function DateFormat({ value, onChange, error }) {
    return html`<div className="col-md-3">
      <label className="form-label">Date format</label>
      <input
        className="form-control"
        value=${value || DEFAULT_DATE_FORMAT}
        onInput=${(event) => onChange(event.target.value)}
      />
      ${error}
    </div>`;
  }

  function FieldForm({ field, errors = {}, onChange, action }) {
    const update = (patch) => onChange({ ...field, ...patch });
    return html`
      <div className="row g-3 align-items-end">
        <div className="col-md-3">
          <label className="form-label">Key</label>
          <input
            className="form-control"
            value=${field.key}
            onInput=${(event) => update({ key: event.target.value })}
          />
          ${errors.key}
        </div>
        <div className="col-md-2">
          <label className="form-label">Type</label>
          <${TypeSelect}
            value=${field.type}
            onChange=${(type) =>
              update({ type, format: type === "date" ? field.format || DEFAULT_DATE_FORMAT : field.format })}
          />
          ${errors.type}
        </div>
        ${field.type === "date"
          ? html`<${DateFormat}
              value=${field.format}
              onChange=${(format) => update({ format })}
              error=${errors.format}
            />`
          : null}
        <div className="col-md-2 pt-md-4">
          <${Required} checked=${field.required} onChange=${(required) => update({ required })} />
        </div>
        <div className="col-md-2 pt-md-4">
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
        ${field.key_label === true
          ? html`<div className="col-md-4">
              <label className="form-label">First hint label</label>
              <input
                className="form-control"
                value=${field.hint_label || ""}
                onInput=${(event) => update({ hint_label: event.target.value })}
              />
              ${errors.hintLabel}
            </div>`
          : null}
        <div className="col-12">
          <label className="form-label">Description</label>
          <input
            className="form-control"
            value=${field.description}
            onInput=${(event) => update({ description: event.target.value })}
          />
          ${errors.description}
        </div>
        ${action ? html`<div className="col-12">${action}</div>` : null}
      </div>
    `;
  }

  function ColumnForm({ column, errors = {}, onChange, onRemove }) {
    const update = (patch) => onChange({ ...column, ...patch });
    return html`
      <div className="row g-2 align-items-start">
        <div className="col-md-4">
          <label className="form-label">Column key</label>
          <input
            className="form-control"
            value=${column.key}
            onInput=${(event) => update({ key: event.target.value })}
          />
          ${errors.key}
        </div>
        <div className="col-md-2">
          <label className="form-label">Type</label>
          <${TypeSelect}
            value=${column.type}
            onChange=${(type) =>
              update({ type, format: type === "date" ? column.format || DEFAULT_DATE_FORMAT : column.format })}
          />
          ${errors.type}
        </div>
        ${column.type === "date"
          ? html`<${DateFormat}
              value=${column.format}
              onChange=${(format) => update({ format })}
              error=${errors.format}
            />`
          : null}
        <div className="col-md-2 pt-md-4">
          <${Required} checked=${column.required} onChange=${(required) => update({ required })} />
        </div>
        <div className="col-md-1 pt-md-4 text-end">
          <button
            className="btn btn-outline-danger btn-sm"
            type="button"
            onClick=${onRemove}
          >
            Remove
          </button>
        </div>
      </div>
    `;
  }

  function TableForm({ table, errors = {}, onChange, children }) {
    const update = (patch) => onChange({ ...table, ...patch });
    return html`
      <div className="row g-3 mb-3 align-items-end">
        <div className="col-md-4">
          <label className="form-label">Key</label>
          <input
            className="form-control"
            value=${table.key}
            onInput=${(event) => update({ key: event.target.value })}
          />
          ${errors.key}
        </div>
        <div className="col-md-3 pt-md-4">
          <${Required} checked=${table.required} onChange=${(required) => update({ required })} />
        </div>
        <div className="col-12">
          <label className="form-label">Description</label>
          <input
            className="form-control"
            value=${table.description}
            onInput=${(event) => update({ description: event.target.value })}
          />
          ${errors.description}
        </div>
      </div>
      ${children}
    `;
  }

  function NewField({ presets, field, hashtag, onField, onHashtag, onAdd }) {
    return html`
      <section className="border rounded p-3">
        <h2 className="h5">Field</h2>
        <${HashtagInput}
          label="Hashtag"
          value=${hashtag}
          presets=${presets.filter((preset) => preset.kind !== "table")}
          onChange=${onHashtag}
          onPick=${(preset) => onField(applyPreset(field, preset))}
        />
        <div className="mt-3">
          <${FieldForm}
            field=${field}
            onChange=${onField}
            action=${html`<button
              className="btn btn-primary"
              type="button"
              onClick=${onAdd}
            >
              Add field
            </button>`}
          />
        </div>
      </section>
    `;
  }

  function ColumnList({ table, tableIndex, columnErrors, onTable }) {
    return html`
      <div className="vstack gap-2">
        ${table.columns.map(
          (column, columnIndex) => html`
            <${ColumnForm}
              key=${columnIndex}
              column=${column}
              errors=${columnErrors?.(tableIndex, columnIndex) || {}}
              onChange=${(next) =>
                onTable({
                  ...table,
                  columns: setAt(table.columns, columnIndex, next),
                })}
              onRemove=${() =>
                onTable({ ...table, columns: removeAt(table.columns, columnIndex) })}
            />
          `,
        )}
      </div>
    `;
  }

  function NewTable({ presets, table, hashtag, onTable, onHashtag, onAdd }) {
    return html`
      <section className="border rounded p-3">
        <h2 className="h5">Table</h2>
        <${HashtagInput}
          label="Hashtag"
          value=${hashtag}
          presets=${presets.filter(
            (preset) => preset.kind === "table" || preset.tag === "#required",
          )}
          onChange=${onHashtag}
          onPick=${(preset) => onTable(applyPreset(table, preset))}
        />
        <div className="mt-3">
          <${TableForm} table=${table} onChange=${onTable}>
            <${ColumnList} table=${table} onTable=${onTable} />
            <div className="d-flex gap-2 mt-3">
              <button
                className="btn btn-outline-secondary"
                type="button"
                onClick=${() =>
                  onTable({ ...table, columns: [...table.columns, blankColumn()] })}
              >
                Add column
              </button>
              <button className="btn btn-primary" type="button" onClick=${onAdd}>
                Add table
              </button>
            </div>
          <//>
        </div>
      </section>
    `;
  }

  function Actions({ onUp, onDown, onRemove }) {
    return html`
      <div className="btn-group btn-group-sm">
        ${[
          ["Up", "outline-secondary", onUp],
          ["Down", "outline-secondary", onDown],
          ["Remove", "outline-danger", onRemove],
        ].map(
          ([label, variant, onClick]) => html`
            <button
              key=${label}
              className=${`btn btn-${variant}`}
              type="button"
              onClick=${onClick}
            >
              ${label}
            </button>
          `,
        )}
      </div>
    `;
  }

  function Draft({ state, setState, errorGroups }) {
    const fieldErrors = (index) => ({
      key: err(errorGroups, `fields[${index}].key`),
      type: err(errorGroups, `fields[${index}].type`),
      format: err(errorGroups, `fields[${index}].format`),
      hintLabel: html`${err(errorGroups, `fields[${index}]`)}${err(
        errorGroups,
        `fields[${index}].hint.labels`,
      )}`,
      description: err(errorGroups, `fields[${index}].description`),
    });
    const tableErrors = (index) => ({
      key: err(errorGroups, `tables[${index}].key`),
      description: err(errorGroups, `tables[${index}].description`),
    });
    const columnErrors = (tableIndex, columnIndex) => ({
      key: err(errorGroups, `tables[${tableIndex}].columns[${columnIndex}].key`),
      type: err(errorGroups, `tables[${tableIndex}].columns[${columnIndex}].type`),
      format: err(
        errorGroups,
        `tables[${tableIndex}].columns[${columnIndex}].format`,
      ),
    });
    const patch = (key, value) => setState((state) => ({ ...state, [key]: value }));

    return html`
      <section>
        <h2 className="h5">Draft</h2>
        <div className="vstack gap-3">
          ${state.fields.map(
            (field, index) => html`
              <div key=${`field-${index}`} className="border rounded p-3">
                <div className="d-flex justify-content-between gap-2 mb-2">
                  <strong>${field.key || "Untitled field"}</strong>
                  <${Actions}
                    onUp=${() => patch("fields", moveItem(state.fields, index, -1))}
                    onDown=${() => patch("fields", moveItem(state.fields, index, 1))}
                    onRemove=${() => patch("fields", removeAt(state.fields, index))}
                  />
                </div>
                <${FieldForm}
                  field=${field}
                  errors=${fieldErrors(index)}
                  onChange=${(next) => patch("fields", setAt(state.fields, index, next))}
                />
              </div>
            `,
          )}
          ${state.tables.map(
            (table, index) => html`
              <div key=${`table-${index}`} className="border rounded p-3">
                <div className="d-flex justify-content-between gap-2 mb-2">
                  <strong>${table.key || "Untitled table"}</strong>
                  <${Actions}
                    onUp=${() => patch("tables", moveItem(state.tables, index, -1))}
                    onDown=${() => patch("tables", moveItem(state.tables, index, 1))}
                    onRemove=${() => patch("tables", removeAt(state.tables, index))}
                  />
                </div>
                <${TableForm}
                  table=${table}
                  errors=${tableErrors(index)}
                  onChange=${(next) => patch("tables", setAt(state.tables, index, next))}
                >
                  <${ColumnList}
                    table=${table}
                    tableIndex=${index}
                    columnErrors=${columnErrors}
                    onTable=${(next) => patch("tables", setAt(state.tables, index, next))}
                  />
                  <button
                    className="btn btn-outline-secondary btn-sm mt-3"
                    type="button"
                    onClick=${() =>
                      patch(
                        "tables",
                        setAt(state.tables, index, {
                          ...table,
                          columns: [...table.columns, blankColumn()],
                        }),
                      )}
                  >
                    Add column
                  </button>
                <//>
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
    const [fieldTag, setFieldTag] = useState("");
    const [tableTag, setTableTag] = useState("");
    const [state, setState] = useState({ fields: [], tables: [] });
    const [errors, setErrors] = useState([]);
    const draft = useMemo(() => buildTemplateDraft(state), [state]);
    const errorGroups = useMemo(() => validationErrorsByPath(errors), [errors]);

    useEffect(() => {
      fetch("/api/presets")
        .then((response) => response.json())
        .then((payload) => setPresets(payload.presets || []));
    }, []);
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
            <${NewField}
              presets=${presets}
              field=${field}
              hashtag=${fieldTag}
              onField=${setField}
              onHashtag=${setFieldTag}
              onAdd=${() => {
                setState((state) => ({ ...state, fields: [...state.fields, field] }));
                setField(blankField());
                setFieldTag("");
              }}
            />
          </div>
          <div className="col-lg-6">
            <${NewTable}
              presets=${presets}
              table=${table}
              hashtag=${tableTag}
              onTable=${setTable}
              onHashtag=${setTableTag}
              onAdd=${() => {
                setState((state) => ({ ...state, tables: [...state.tables, table] }));
                setTable(blankTable());
                setTableTag("");
              }}
            />
          </div>
        </div>
        <${Draft} state=${state} setState=${setState} errorGroups=${errorGroups} />
      </div>
    `;
  }

  ReactDOM.createRoot(root).render(html`<${FieldEditor} />`);
})();
