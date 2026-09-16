import { apiFetch } from "./client.js";

export const listSources = () => apiFetch("/api/sources/");
export const createSource = (payload) => apiFetch("/api/sources/", { method: "POST", body: payload });
export const updateSource = (id, payload) => apiFetch(`/api/sources/${id}`, { method: "PUT", body: payload });
export const deleteSource = (id) => apiFetch(`/api/sources/${id}`, { method: "DELETE" });
export const testSourceAdhoc = (payload) => apiFetch("/api/sources/test", { method: "POST", body: payload });
export const testSource = (id) => apiFetch(`/api/sources/${id}/test`, { method: "POST" });
export const introspectSource = (id, bucket) => apiFetch(`/api/sources/${id}/introspect${bucket ? `?bucket=${encodeURIComponent(bucket)}` : ""}`);
export const listTableColumns = (id, schema, table) => apiFetch(`/api/sources/${id}/tables/${encodeURIComponent(schema)}/${encodeURIComponent(table)}/columns`);
export const listAnnotations = (id) => apiFetch(`/api/sources/${id}/annotations`);
export const upsertAnnotations = (id, items) => apiFetch(`/api/sources/${id}/annotations`, { method: "PUT", body: items });
export const deleteAnnotation = (id, annotationId) => apiFetch(`/api/sources/${id}/annotations/${annotationId}`, { method: "DELETE" });
