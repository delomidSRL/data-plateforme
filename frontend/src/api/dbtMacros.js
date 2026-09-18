import { apiFetch } from "./client.js";

// UX ask — platform-wide, admin-managed (mirrors mlTemplates.js): read is open to any
// authenticated user (offered in every project's 03_standardized SQL editor), writes are
// admin-only (enforced server-side, app/api/routes/dbt_macros.py).
const base = "/api/dbt-macros";

export const listMacros = () => apiFetch(`${base}/`);
export const createMacro = (payload) => apiFetch(`${base}/`, { method: "POST", body: payload });
export const updateMacro = (id, payload) => apiFetch(`${base}/${id}`, { method: "PUT", body: payload });
export const deleteMacro = (id) => apiFetch(`${base}/${id}`, { method: "DELETE" });
