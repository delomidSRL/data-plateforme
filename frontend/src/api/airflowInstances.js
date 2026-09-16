import { apiFetch } from "./client.js";

const base = (iid) => `/api/airflow-instances/${iid}`;

export const listInstances = () => apiFetch("/api/airflow-instances/");
export const createInstance = (payload) => apiFetch("/api/airflow-instances/", { method: "POST", body: payload });
export const getInstance = (iid) => apiFetch(base(iid));
export const updateInstance = (iid, payload) => apiFetch(base(iid), { method: "PUT", body: payload });
export const deleteInstance = (iid) => apiFetch(base(iid), { method: "DELETE" });
export const testInstance = (iid) => apiFetch(`${base(iid)}/test`, { method: "POST" });
export const testInstanceAdhoc = (payload) => apiFetch("/api/airflow-instances/test", { method: "POST", body: payload });

export const listDags = (iid) => apiFetch(`${base(iid)}/dags`);
export const setDagPaused = (iid, dagId, isPaused) => apiFetch(`${base(iid)}/dags/${dagId}`, { method: "PATCH", body: { is_paused: isPaused } });
export const triggerDagRun = (iid, dagId, payload = {}) => apiFetch(`${base(iid)}/dags/${dagId}/runs`, { method: "POST", body: payload });
export const listDagRuns = (iid, dagId) => apiFetch(`${base(iid)}/dags/${dagId}/runs`);
export const getDagRun = (iid, dagId, runId) => apiFetch(`${base(iid)}/dags/${dagId}/runs/${runId}`);

export const listConnections = (iid) => apiFetch(`${base(iid)}/connections`);
export const createConnection = (iid, payload) => apiFetch(`${base(iid)}/connections`, { method: "POST", body: payload });
export const testConnection = (iid, payload) => apiFetch(`${base(iid)}/connections/test`, { method: "POST", body: payload });

export const setDeployAccess = (iid, payload) => apiFetch(`${base(iid)}/deploy-access`, { method: "PUT", body: payload });
export const runPreflight = (iid) => apiFetch(`${base(iid)}/preflight`, { method: "POST" });
export const getPreflight = (iid) => apiFetch(`${base(iid)}/preflight`);
