import { apiFetch } from "./client.js";

export const listServers = () => apiFetch("/api/servers/");
export const getServer = (id) => apiFetch(`/api/servers/${id}`);
export const createServer = (payload) => apiFetch("/api/servers/", { method: "POST", body: payload });
export const updateServer = (id, payload) => apiFetch(`/api/servers/${id}`, { method: "PUT", body: payload });
export const deleteServer = (id) => apiFetch(`/api/servers/${id}`, { method: "DELETE" });
export const testServer = (id) => apiFetch(`/api/servers/${id}/test`, { method: "POST" });
export const testServerAdhoc = (payload) => apiFetch("/api/servers/test", { method: "POST", body: payload });
