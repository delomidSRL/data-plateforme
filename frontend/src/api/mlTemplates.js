import { apiFetch } from "./client.js";

const base = "/api/ml-templates";

export const listTemplates = () => apiFetch(`${base}/`);
export const getTemplate = (id) => apiFetch(`${base}/${id}`);
export const createTemplate = (payload) => apiFetch(`${base}/`, { method: "POST", body: payload });
export const updateTemplate = (id, payload) => apiFetch(`${base}/${id}`, { method: "PUT", body: payload });
export const deleteTemplate = (id) => apiFetch(`${base}/${id}`, { method: "DELETE" });
