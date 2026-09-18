export const EXPORTABLE_STATUSES = new Set(["passed", "reviewed"]);

export function documentStatus(document, results) {
  return results[document.result_id]?.status ?? "missing";
}

export function isSelectableDocument(document, results) {
  return EXPORTABLE_STATUSES.has(documentStatus(document, results));
}

export function selectAllDocuments(manifest, results) {
  return manifest.documents
    .filter((document) => isSelectableDocument(document, results))
    .map((document) => document.document_id);
}

export function toggleDocumentSelection(selectedIds, documentId, manifest, results) {
  const document = manifest.documents.find((item) => item.document_id === documentId);
  if (!document || !isSelectableDocument(document, results)) {
    return [...selectedIds];
  }
  if (selectedIds.includes(documentId)) {
    return selectedIds.filter((selectedId) => selectedId !== documentId);
  }
  return [...selectedIds, documentId];
}

export function selectedCountLabel(selectedIds) {
  const count = selectedIds.length;
  return `${count} selected`;
}
