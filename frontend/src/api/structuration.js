import { apiFetch } from "./client.js";

const base = "/api/medallion/projects";

// Module 6 extension — payload & structuration.
export const profileStructuration = (pid, did) => apiFetch(`${base}/${pid}/datasets/${did}/structuration/profile`, { method: "POST" });
export const getStructuration = (pid, did) => apiFetch(`${base}/${pid}/datasets/${did}/structuration`);
export const saveStructuration = (pid, did, payload) => apiFetch(`${base}/${pid}/datasets/${did}/structuration`, { method: "PUT", body: payload });

// Module 18 §7 UX — instant preview of silver.01_unpacked_<name>/silver.02_typed_<name>
// (materialize_unpacked_typed_sync), same response shape as medallionApi.getDatasetPreview.
export const previewStructurationTable = (pid, did, stage, { limit = 50, offset = 0 } = {}) => {
  const params = new URLSearchParams({ stage, limit: String(limit), offset: String(offset) });
  return apiFetch(`${base}/${pid}/datasets/${did}/structuration/preview?${params.toString()}`);
};
