import { apiFetch } from "./client.js";

const base = (serverId, sid) => `/api/servers/${serverId}/stacks/${sid}/airflow`;

export const getAirflowToken = (serverId, sid) => apiFetch(`${base(serverId, sid)}/token`, { method: "POST" });
export const listConnections = (serverId, sid) => apiFetch(`${base(serverId, sid)}/connections`);
export const createConnection = (serverId, sid, payload) => apiFetch(`${base(serverId, sid)}/connections`, { method: "POST", body: payload });
export const testConnection = (serverId, sid, payload) => apiFetch(`${base(serverId, sid)}/connections/test`, { method: "POST", body: payload });
export const deployDagFile = (serverId, sid, payload) => apiFetch(`${base(serverId, sid)}/dags`, { method: "POST", body: payload });
export const listDags = (serverId, sid) => apiFetch(`${base(serverId, sid)}/dags`);
export const setDagPaused = (serverId, sid, dagId, isPaused) => apiFetch(`${base(serverId, sid)}/dags/${dagId}`, { method: "PATCH", body: { is_paused: isPaused } });
export const triggerDagRun = (serverId, sid, dagId, payload) => apiFetch(`${base(serverId, sid)}/dags/${dagId}/runs`, { method: "POST", body: payload });
export const getDagRun = (serverId, sid, dagId, runId) => apiFetch(`${base(serverId, sid)}/dags/${dagId}/runs/${runId}`);
