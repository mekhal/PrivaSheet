export const DEFAULT_DATE_FORMAT = "DD/MM/YYYY";
export const FIELD_TYPES = ["text", "date", "decimal"];

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

export function blankField() {
  return { key: "", type: "text", required: true, key_label: false, description: "", format: DEFAULT_DATE_FORMAT };
}

export function blankColumn() {
  return { key: "", type: "text", required: true, format: DEFAULT_DATE_FORMAT };
}

export function blankTable() {
  return { key: "", required: true, description: "", columns: [blankColumn()] };
}

export function filterPresetSuggestions(query, presets, limit = 8) {
  const raw = String(query || "").trim().toLowerCase();
  if (!raw) {
    return [];
  }
  const normalized = raw.startsWith("#") ? raw : `#${raw}`;
  return presets
    .filter((preset) => preset.tag && preset.tag.toLowerCase().startsWith(normalized))
    .slice(0, limit)
    .map(clone);
}

export function applyPreset(base, preset) {
  if (preset.tag === "#required") {
    return { ...base, required: !base.required };
  }

  const next = { ...base, key: preset.key || base.key || "", type: preset.type === "table" ? base.type || "text" : preset.type || base.type, required: base.required ?? true, description: preset.description || base.description || "" };

  if (next.type === "date") {
    next.format = preset.format || base.format || DEFAULT_DATE_FORMAT;
  }

  if (preset.kind === "table") {
    return { key: preset.key || base.key || "", required: base.required ?? true, description: preset.description || base.description || "", columns: (preset.columns || []).map((column) => ({ key: column.key || "", type: column.type || "text", required: column.required ?? true, format: column.format || DEFAULT_DATE_FORMAT })) };
  }

  return next;
}

function cleanedField(field) {
  const item = { key: String(field.key || "").trim(), type: field.type, required: field.required !== false, key_label: field.key_label === true, description: String(field.description || "").trim() };
  if (item.type === "date") {
    item.format = String(field.format || DEFAULT_DATE_FORMAT).trim();
  }
  return item;
}

function cleanedColumn(column) {
  const item = { key: String(column.key || "").trim(), type: column.type, required: column.required !== false };
  if (item.type === "date") {
    item.format = String(column.format || DEFAULT_DATE_FORMAT).trim();
  }
  return item;
}

export function buildTemplateDraft(state) {
  return {
    fields: (state.fields || []).map(cleanedField),
    tables: (state.tables || []).map((table) => ({ key: String(table.key || "").trim(), required: table.required !== false, description: String(table.description || "").trim(), columns: (table.columns || []).map(cleanedColumn) })),
  };
}

export function moveItem(items, index, direction) {
  const nextIndex = index + direction;
  if (nextIndex < 0 || nextIndex >= items.length) {
    return items.slice();
  }
  const next = items.slice();
  const [item] = next.splice(index, 1);
  next.splice(nextIndex, 0, item);
  return next;
}

export function validationErrorsByPath(errors) {
  return (errors || []).reduce((groups, error) => {
    const path = String(error).split(" ", 1)[0];
    groups[path] = [...(groups[path] || []), error];
    return groups;
  }, {});
}
