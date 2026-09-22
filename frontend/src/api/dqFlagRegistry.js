import { apiFetch } from "./client.js";

// UX ask — platform-wide, admin-managed (mirrors dbtMacros.js): read is open to any
// authenticated user (their hand-written 04_annotated/05_validated/05_quarantine SQL can
// ref() it), writes are admin-only (enforced server-side, app/api/routes/dq_flag_registry.py).
const base = "/api/dq-flag-registry";

export const listEntries = () => apiFetch(`${base}/`);
export const createEntry = (payload) => apiFetch(`${base}/`, { method: "POST", body: payload });
export const updateEntry = (id, payload) => apiFetch(`${base}/${id}`, { method: "PUT", body: payload });
export const deleteEntry = (id) => apiFetch(`${base}/${id}`, { method: "DELETE" });
