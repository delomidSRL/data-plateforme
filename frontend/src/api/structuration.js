import { apiFetch } from "./client.js";

const base = "/api/medallion/projects";

// Module 6 extension — payload & structuration.
export const profileStructuration = (pid, did) => apiFetch(`${base}/${pid}/datasets/${did}/structuration/profile`, { method: "POST" });
export const getStructuration = (pid, did) => apiFetch(`${base}/${pid}/datasets/${did}/structuration`);
export const saveStructuration = (pid, did, payload) => apiFetch(`${base}/${pid}/datasets/${did}/structuration`, { method: "PUT", body: payload });

export const listQuarantine = (pid, did, { column, limit = 50, offset = 0 } = {}) => {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (column) params.set("column", column);
  return apiFetch(`${base}/${pid}/datasets/${did}/structuration/quarantine?${params.toString()}`);
};
export const getQuarantineSummary = (pid, did) => apiFetch(`${base}/${pid}/datasets/${did}/structuration/quarantine/summary`);

// Module 18 §7 UX — instant preview of silver.typed_<name>/silver.structured_<name>
// (materialize_typed_structured_sync), same response shape as medallionApi.getDatasetPreview.
export const previewStructurationTable = (pid, did, stage, { limit = 50, offset = 0 } = {}) => {
  const params = new URLSearchParams({ stage, limit: String(limit), offset: String(offset) });
  return apiFetch(`${base}/${pid}/datasets/${did}/structuration/preview?${params.toString()}`);
};
