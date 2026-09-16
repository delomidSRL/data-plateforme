import { apiFetch } from "./client.js";

const base = (pid) => `/api/medallion/projects/${pid}`;

// Module 17 — environment bindings & promotion.
export const listBindings = (pid) => apiFetch(`${base(pid)}/bindings`);
export const upsertProdBinding = (pid, payload) => apiFetch(`${base(pid)}/bindings/prod`, { method: "POST", body: payload });
export const listSourceMappings = (pid) => apiFetch(`${base(pid)}/bindings/prod/source-mappings`);
export const confirmSourceMapping = (pid, originSourceId, targetSourceId) =>
  apiFetch(`${base(pid)}/bindings/prod/source-mappings/${originSourceId}`, { method: "PUT", body: { target_source_id: targetSourceId } });
export const getPromotionPreview = (pid) => apiFetch(`${base(pid)}/promote/preview`);
export const promote = (pid) => apiFetch(`${base(pid)}/promote`, { method: "POST", body: { confirm: true } });
