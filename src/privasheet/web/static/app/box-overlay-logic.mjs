const FIELD_COLORS = [
  "#0d6efd",
  "#198754",
  "#dc3545",
  "#6f42c1",
  "#fd7e14",
  "#0dcaf0",
  "#d63384",
  "#20c997",
];

export function fieldColor(fieldKey) {
  const knownIndex = ["invoice_no", "total", "date"].indexOf(fieldKey);
  if (knownIndex >= 0) {
    return FIELD_COLORS[knownIndex];
  }

  let hash = 0;
  for (const character of String(fieldKey)) {
    hash = (hash * 31 + character.charCodeAt(0)) >>> 0;
  }
  return FIELD_COLORS[hash % FIELD_COLORS.length];
}

export function quadToSvgPoints(quad, page) {
  const width = Number(page?.width || 1);
  const height = Number(page?.height || 1);
  return (quad || [])
    .map((point) => {
      const x = Array.isArray(point) ? point[0] : point?.x;
      const y = Array.isArray(point) ? point[1] : point?.y;
      return `${formatPoint(Number(x) * width)},${formatPoint(Number(y) * height)}`;
    })
    .join(" ");
}

export function assignmentsForBox(assignments, boxId) {
  return Object.entries(assignments || {})
    .filter(([, boxIds]) => Array.isArray(boxIds) && boxIds.includes(boxId))
    .map(([fieldKey]) => fieldKey);
}

export function groundSpan(snapshot, boxIds, span) {
  const normalizedSpan = collapseWhitespace(span);
  if (!normalizedSpan || !Array.isArray(boxIds) || boxIds.length === 0) {
    return false;
  }

  const textById = new Map();
  for (const page of snapshot?.pages || []) {
    for (const box of page.boxes || []) {
      textById.set(box.id, box.text || "");
    }
  }

  const sourceParts = [];
  for (const boxId of boxIds) {
    if (!textById.has(boxId)) {
      return false;
    }
    sourceParts.push(textById.get(boxId));
  }

  return collapseWhitespace(sourceParts.join(" ")).includes(normalizedSpan);
}

export function evidenceForAssignments(assignments, spans, snapshot) {
  const fields = {};
  for (const [fieldKey, boxIds] of Object.entries(assignments || {})) {
    const span = spans?.[fieldKey] || "";
    fields[fieldKey] = {
      box_ids: boxIds,
      span,
      grounded: span ? groundSpan(snapshot, boxIds, span) : null,
    };
  }
  return { fields };
}

function collapseWhitespace(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function formatPoint(value) {
  return Number.isFinite(value)
    ? Number.parseFloat(value.toFixed(3)).toString()
    : "0";
}
