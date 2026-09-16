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
