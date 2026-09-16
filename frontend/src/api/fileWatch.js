import { apiFetch } from "./client.js";

const base = "/api/watches";

export const listWatches = () => apiFetch(`${base}/`);
export const getWatch = (id) => apiFetch(`${base}/${id}`);
export const createWatch = (payload) => apiFetch(`${base}/`, { method: "POST", body: payload });
export const updateWatch = (id, payload) => apiFetch(`${base}/${id}`, { method: "PUT", body: payload });
export const pauseWatch = (id) => apiFetch(`${base}/${id}/pause`, { method: "POST" });
export const resumeWatch = (id) => apiFetch(`${base}/${id}/resume`, { method: "POST" });
export const testWatch = (id) => apiFetch(`${base}/${id}/test`, { method: "POST" });
export const testWatchDraft = (payload) => apiFetch(`${base}/test`, { method: "POST", body: payload });
export const listWatchEvents = (id, outcome) => apiFetch(`${base}/${id}/events${outcome ? `?outcome=${outcome}` : ""}`);
export const ackWatchEvent = (watchId, eventId) => apiFetch(`${base}/${watchId}/events/${eventId}/ack`, { method: "POST" });
export const deleteWatch = (id) => apiFetch(`${base}/${id}`, { method: "DELETE" });
