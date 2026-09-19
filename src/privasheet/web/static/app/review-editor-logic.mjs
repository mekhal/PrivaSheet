export const REVIEWABLE_STATUSES = new Set([
  "passed",
  "needs_review",
  "reviewed",
  "rejected",
]);

const DECIMAL_PATTERN = /^-?(?:0|[1-9]\d*)(?:\.\d+)?$/;
const DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function extractedValue(cell) {
  if (!cell || typeof cell !== "object" || !Object.hasOwn(cell, "value")) {
    return "";
  }
  return cell.value ?? "";
}

function sourceFieldValue(result, field) {
  if (result.review?.fields && Object.hasOwn(result.review.fields, field.key)) {
    return result.review.fields[field.key] ?? "";
  }
  return extractedValue(result.extracted?.fields?.[field.key]);
}

function sourceTableRows(result, table) {
  if (result.review?.tables && Array.isArray(result.review.tables[table.key])) {
    return result.review.tables[table.key];
  }
  return result.extracted?.tables?.[table.key] || [];
}

function labelFor(item) {
  return item.label || item.key;
}

function isValidDate(value) {
  if (!DATE_PATTERN.test(value)) {
    return false;
  }
  const parsed = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(parsed.valueOf()) && parsed.toISOString().slice(0, 10) === value;
}

function validateValue(item, value) {
  if (value === "") {
    if (item.required) {
      return `${labelFor(item)} is required.`;
    }
    return "";
  }
  if (item.type === "decimal" && !DECIMAL_PATTERN.test(value)) {
    return `${labelFor(item)} must be a plain decimal like 1284.00.`;
  }
  if (item.type === "date" && !isValidDate(value)) {
    return `${labelFor(item)} must use YYYY-MM-DD.`;
  }
  return "";
}

function normalizeValue(item, value) {
  if (value === "" && !item.required) {
    return null;
  }
  return value;
}

export function createDraft(result) {
  const draft = { fields: {}, tables: {} };
  for (const field of result.schema.fields || []) {
    draft.fields[field.key] = sourceFieldValue(result, field);
  }
  for (const table of result.schema.tables || []) {
    draft.tables[table.key] = sourceTableRows(result, table).map((row) => {
      const draftRow = {};
      for (const column of table.columns || []) {
        const value = row[column.key];
        draftRow[column.key] =
          value && typeof value === "object" && Object.hasOwn(value, "value")
            ? extractedValue(value)
            : value ?? "";
      }
      return draftRow;
    });
  }
  return draft;
}

export function addTableRow(result, draft, tableKey) {
  const table = (result.schema.tables || []).find((candidate) => candidate.key === tableKey);
  if (!table) {
    return clone(draft);
  }
  const row = Object.fromEntries((table.columns || []).map((column) => [column.key, ""]));
  return {
    ...clone(draft),
    tables: {
      ...clone(draft.tables),
      [tableKey]: [...(draft.tables[tableKey] || []), row],
    },
  };
}

export function removeTableRow(draft, tableKey, rowIndex) {
  return {
    ...clone(draft),
    tables: {
      ...clone(draft.tables),
      [tableKey]: (draft.tables[tableKey] || []).filter((_, index) => index !== rowIndex),
    },
  };
}

export function draftIssues(result, draft) {
  const issues = [];
  for (const field of result.schema.fields || []) {
    const message = validateValue(field, draft.fields[field.key] ?? "");
    if (message) {
      issues.push({ path: `fields.${field.key}`, message });
    }
  }
  for (const table of result.schema.tables || []) {
    for (const [rowIndex, row] of (draft.tables[table.key] || []).entries()) {
      for (const column of table.columns || []) {
        const message = validateValue(column, row[column.key] ?? "");
        if (message) {
          issues.push({
            path: `tables.${table.key}.${rowIndex}.${column.key}`,
            message,
          });
        }
      }
    }
  }
  return issues;
}

export function acknowledgementKey(issue, index) {
  return `${issue.id ?? index}:${issue.code}:${issue.target || "document"}`;
}

export function allIssuesAcknowledged(result, acknowledgedIssues) {
  const acknowledged = new Set(acknowledgedIssues || []);
  return (result.issues || []).every((issue, index) =>
    acknowledged.has(acknowledgementKey(issue, index)),
  );
}

export function buildDuplicateHref(issue) {
  if (issue.code !== "DUPLICATE_DOCUMENT" || !issue.document_id) {
    return null;
  }
  return `/review?document_id=${encodeURIComponent(issue.document_id)}`;
}

export function buildReviewPayload(result, draft, acknowledgedIssues) {
  const fields = {};
  for (const field of result.schema.fields || []) {
    fields[field.key] = normalizeValue(field, draft.fields[field.key] ?? "");
  }

  const tables = {};
  for (const table of result.schema.tables || []) {
    tables[table.key] = (draft.tables[table.key] || []).map((row) => {
      const payloadRow = {};
      for (const column of table.columns || []) {
        payloadRow[column.key] = normalizeValue(column, row[column.key] ?? "");
      }
      return payloadRow;
    });
  }

  return {
    fields,
    tables,
    acknowledged: {
      revision: result.revision,
      issues: [...(acknowledgedIssues || [])],
    },
  };
}

function sameValues(row, storedRow) {
  const keys = new Set([...Object.keys(row || {}), ...Object.keys(storedRow || {})]);
  for (const key of keys) {
    if ((row?.[key] ?? "") !== (storedRow?.[key] ?? "")) {
      return false;
    }
  }
  return true;
}

export function isDirty(draft, storedDraft) {
  if (!sameValues(draft?.fields, storedDraft?.fields)) {
    return true;
  }
  const tableKeys = new Set([
    ...Object.keys(draft?.tables || {}),
    ...Object.keys(storedDraft?.tables || {}),
  ]);
  for (const key of tableKeys) {
    const rows = draft?.tables?.[key] || [];
    const storedRows = storedDraft?.tables?.[key] || [];
    if (rows.length !== storedRows.length) {
      return true;
    }
    for (const [index, row] of rows.entries()) {
      if (!sameValues(row, storedRows[index])) {
        return true;
      }
    }
  }
  return false;
}

export function actionState(status, dirty, validationIssues, acknowledged) {
  const editable = REVIEWABLE_STATUSES.has(status);
  const valid = validationIssues.length === 0;
  const ready = editable && valid && acknowledged;
  return {
    canEdit: REVIEWABLE_STATUSES.has(status) && !dirty,
    canSave: editable && valid,
    canCancel: editable,
    canMarkReviewed: status === "needs_review" && ready,
    canReject: status === "needs_review" && ready,
    canDelete: status !== "deleted",
  };
}

function assertReady(result, draft, acknowledgedIssues) {
  const issues = draftIssues(result, draft);
  if (issues.length > 0) {
    throw new Error("Draft has validation issues.");
  }
  if (!allIssuesAcknowledged(result, acknowledgedIssues)) {
    throw new Error("All issues must be acknowledged.");
  }
}

export function saveDraft(result, draft, acknowledgedIssues) {
  const issues = draftIssues(result, draft);
  if (issues.length > 0) {
    throw new Error("Draft has validation issues.");
  }
  return {
    ...result,
    status: "needs_review",
    review: buildReviewPayload(result, draft, acknowledgedIssues),
    editing: true,
  };
}

export function cancelDraft(result) {
  return {
    draft: createDraft(result),
    acknowledgedIssues: result.review?.acknowledged?.issues || [],
    editing: false,
  };
}

export function markReviewed(result, draft, acknowledgedIssues) {
  assertReady(result, draft, acknowledgedIssues);
  return {
    ...result,
    status: "reviewed",
    review: buildReviewPayload(result, draft, acknowledgedIssues),
  };
}

export function markRejected(result, draft, acknowledgedIssues) {
  assertReady(result, draft, acknowledgedIssues);
  return {
    ...result,
    status: "rejected",
    review: buildReviewPayload(result, draft, acknowledgedIssues),
  };
}
