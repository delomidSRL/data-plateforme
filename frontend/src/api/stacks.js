import { apiFetch } from "./client.js";

export const listStacks = (serverId) => apiFetch(`/api/servers/${serverId}/stacks/`);
export const createStack = (serverId, payload) => apiFetch(`/api/servers/${serverId}/stacks/`, { method: "POST", body: payload });
export const updateStack = (serverId, sid, payload) => apiFetch(`/api/servers/${serverId}/stacks/${sid}`, { method: "PUT", body: payload });
export const previewStack = (serverId, sid) => apiFetch(`/api/servers/${serverId}/stacks/${sid}/preview`);
export const deployStack = (serverId, sid) => apiFetch(`/api/servers/${serverId}/stacks/${sid}/deploy`, { method: "POST" });
export const stopStack = (serverId, sid) => apiFetch(`/api/servers/${serverId}/stacks/${sid}/stop`, { method: "POST" });
export const restartStack = (serverId, sid) => apiFetch(`/api/servers/${serverId}/stacks/${sid}/restart`, { method: "POST" });
export const stackStatus = (serverId, sid) => apiFetch(`/api/servers/${serverId}/stacks/${sid}/status`);
export const verifyStack = (serverId, sid) => apiFetch(`/api/servers/${serverId}/stacks/${sid}/verify`, { method: "POST" });
export const deleteStack = (serverId, sid) => apiFetch(`/api/servers/${serverId}/stacks/${sid}`, { method: "DELETE" });
