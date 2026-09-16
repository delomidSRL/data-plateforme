import { apiDownload, apiFetch } from "./client.js";

const base = "/api/medallion/projects";

export const getProjectQuality = (pid) => apiFetch(`${base}/${pid}/quality`);
export const getDatasetQualityHistory = (pid, did, limit = 30) => apiFetch(`${base}/${pid}/datasets/${did}/quality/history?limit=${limit}`);
export const collectQuality = (pid) => apiFetch(`${base}/${pid}/quality/collect`, { method: "POST" });

// Module 16 — intrinsic quality (orthogonal to the drift signals above): the pipeline x layer
// matrix (§5.3), and the raw auto-portant/contract metrics behind it (§4.4).
export const getQualityOverview = (pid, runId) => apiFetch(`${base}/${pid}/quality/overview${runId ? `?run_id=${runId}` : ""}`);
export const getQualityMetrics = (pid, runId) => apiFetch(`${base}/${pid}/quality/metrics${runId ? `?run_id=${runId}` : ""}`);
export const recomputeQualityMetrics = (pid) => apiFetch(`${base}/${pid}/quality/metrics/recompute`, { method: "POST" });

// Module 16 §6 — baseline (déclaration engineer + brouillon IA §6.3)
export const getQualityBaseline = (pid) => apiFetch(`${base}/${pid}/quality/baseline`);
export const updateQualityBaseline = (pid, payload) => apiFetch(`${base}/${pid}/quality/baseline`, { method: "PUT", body: payload });
export const suggestQualityBaseline = (pid) => apiFetch(`${base}/${pid}/quality/baseline/suggest`, { method: "POST" });

// Module 16 §7 — contrat de qualité paramétré (checks)
export const listQualityChecks = (pid, datasetId) => apiFetch(`${base}/${pid}/quality/checks${datasetId ? `?dataset_id=${datasetId}` : ""}`);
export const suggestQualityChecks = (pid, datasetId) => apiFetch(`${base}/${pid}/quality/checks/suggest?dataset_id=${datasetId}`, { method: "POST" });
export const createQualityCheck = (pid, payload) => apiFetch(`${base}/${pid}/quality/checks`, { method: "POST", body: payload });
export const updateQualityCheck = (pid, checkId, payload) => apiFetch(`${base}/${pid}/quality/checks/${checkId}`, { method: "PUT", body: payload });
export const deleteQualityCheck = (pid, checkId) => apiFetch(`${base}/${pid}/quality/checks/${checkId}`, { method: "DELETE" });

export const getQualityRules = (pid) => apiFetch(`${base}/${pid}/quality/rules`);
export const updateQualityRules = (pid, payload) => apiFetch(`${base}/${pid}/quality/rules`, { method: "PUT", body: payload });

export const listQualityAlerts = (pid, status) => apiFetch(`${base}/${pid}/quality/alerts${status ? `?status=${status}` : ""}`);
export const ackQualityAlert = (pid, alertId) => apiFetch(`${base}/${pid}/quality/alerts/${alertId}/ack`, { method: "POST" });
export const explainQualityAlert = (pid, alertId) => apiFetch(`${base}/${pid}/quality/alerts/${alertId}/explain`, { method: "POST" });

// Module 16 extension §6 — export du projet dbt autoportant
export const previewDbtExport = (pid) => apiFetch(`${base}/${pid}/quality/export/dbt/preview`);
export const exportDbtProject = (pid) => apiDownload(`${base}/${pid}/quality/export/dbt`);

export const listChannels = (pid) => apiFetch(`${base}/${pid}/quality/channels`);
export const createChannel = (pid, payload) => apiFetch(`${base}/${pid}/quality/channels`, { method: "POST", body: payload });
export const updateChannel = (pid, channelId, payload) => apiFetch(`${base}/${pid}/quality/channels/${channelId}`, { method: "PUT", body: payload });
export const deleteChannel = (pid, channelId) => apiFetch(`${base}/${pid}/quality/channels/${channelId}`, { method: "DELETE" });
export const testChannel = (pid, channelId) => apiFetch(`${base}/${pid}/quality/channels/${channelId}/test`, { method: "POST" });
