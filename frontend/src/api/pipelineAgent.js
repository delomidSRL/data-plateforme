import { apiFetch } from "./client.js";

const base = (pid) => `/api/medallion/projects/${pid}/agent`;

export const mapIntent = (pid, instruction) => apiFetch(`${base(pid)}/map-intent`, { method: "POST", body: { instruction } });
export const remap = (pid) => apiFetch(`${base(pid)}/remap`, { method: "POST" });
export const getPlan = (pid) => apiFetch(`${base(pid)}/plan`);
export const generatePlan = (pid) => apiFetch(`${base(pid)}/plan`, { method: "POST" });
export const replan = (pid) => apiFetch(`${base(pid)}/replan`, { method: "POST", body: { confirm: true } });
export const patchPlan = (pid, payload) => apiFetch(`${base(pid)}/plan`, { method: "PATCH", body: payload });
export const execute = (pid) => apiFetch(`${base(pid)}/execute`, { method: "POST" });
export const getExecution = (pid) => apiFetch(`${base(pid)}/execution`);
export const approveSilverDuringExecution = (pid, datasetId) => apiFetch(`${base(pid)}/silver/${datasetId}/approve`, { method: "POST" });
export const getRelationships = (pid) => apiFetch(`${base(pid)}/relationships`);
export const recomputeRelationships = (pid) => apiFetch(`${base(pid)}/relationships/recompute`, { method: "POST" });
export const repairSilver = (pid, name) => apiFetch(`${base(pid)}/silver/${encodeURIComponent(name)}/repair`, { method: "POST" });
export const generateDashboards = (pid, payload) => apiFetch(`${base(pid)}/dashboard/generate`, { method: "POST", body: payload });
