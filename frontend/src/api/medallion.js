import { apiDownload, apiFetch } from "./client.js";

const base = "/api/medallion/projects";

export const listProjects = () => apiFetch(`${base}/`);
export const createProject = (payload) => apiFetch(`${base}/`, { method: "POST", body: payload });
export const getProject = (pid) => apiFetch(`${base}/${pid}`);
export const updateProject = (pid, payload) => apiFetch(`${base}/${pid}`, { method: "PUT", body: payload });
export const deleteProject = (pid) => apiFetch(`${base}/${pid}`, { method: "DELETE" });
// Module 15 — the only write path for a project's folder_id; PUT /projects/{pid} ignores it.
export const moveProjectFolder = (pid, folderId) => apiFetch(`${base}/${pid}/folder`, { method: "PATCH", body: { folder_id: folderId } });

export const listDatasets = (pid) => apiFetch(`${base}/${pid}/datasets`);
export const createDataset = (pid, payload) => apiFetch(`${base}/${pid}/datasets`, { method: "POST", body: payload });
// UX ask — the same payload/typed choice the standalone Imports wizard offers, applied to a
// CSV/Excel file already sitting in an S3/MinIO bucket, browsed from the bronze dataset picker
// right here instead of uploading a local file. Returns a FileImportOut, same shape the
// Imports page itself works with (status="awaiting_validation" needs SchemaValidationModal,
// same as there; payload mode is already imported).
export const importFromObjectStore = (pid, payload) => apiFetch(`${base}/${pid}/import-from-object-store`, { method: "POST", body: payload });
// Scratch analysis only (no FileImport row) — mirrors imports.getColumns for a file already
// sitting in a bucket, used for the source_pk composite-key candidate list.
export const getObjectStoreColumns = (pid, payload) => apiFetch(`${base}/${pid}/import-from-object-store/columns`, { method: "POST", body: payload });
export const updateDataset = (pid, did, payload) => apiFetch(`${base}/${pid}/datasets/${did}`, { method: "PUT", body: payload });
export const deleteDataset = (pid, did) => apiFetch(`${base}/${pid}/datasets/${did}`, { method: "DELETE" });
export const getDatasetColumns = (pid, did) => apiFetch(`${base}/${pid}/datasets/${did}/columns`);
// Module 10 — bounded, read-only sample of a dataset's real table (or its upstream source
// when `source: true`, used for bronze-not-materialized and origin nodes).
export const getDatasetPreview = (pid, did, { limit = 50, offset = 0, source = false } = {}) => {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (source) params.set("source", "true");
  return apiFetch(`${base}/${pid}/datasets/${did}/preview?${params.toString()}`);
};
// Module 11 extension — stream a whole gold table as CSV (returns a Blob), and the recent
// export journal for a project.
export const exportDatasetCsv = (pid, did) => apiDownload(`${base}/${pid}/datasets/${did}/export.csv`);
export const listProjectExports = (pid, limit = 20) => apiFetch(`${base}/${pid}/exports?limit=${limit}`);

export const previewProject = (pid) => apiFetch(`${base}/${pid}/preview`, { method: "POST" });
// UX ask — dataset editor's "Valider la syntaxe": checks ad-hoc dbt SQL (ref()/source() +
// a real EXPLAIN) without saving anything.
export const validateSql = (pid, sql) => apiFetch(`${base}/${pid}/validate-sql`, { method: "POST", body: { sql } });
export const buildProject = (pid) => apiFetch(`${base}/${pid}/build`, { method: "POST" });
export const getDeployStatus = (pid) => apiFetch(`${base}/${pid}/deploy-status`);
export const runProject = (pid, payload = {}) => apiFetch(`${base}/${pid}/run`, { method: "POST", body: payload });
export const pauseProject = (pid) => apiFetch(`${base}/${pid}/pause`, { method: "POST" });
export const unpauseProject = (pid) => apiFetch(`${base}/${pid}/unpause`, { method: "POST" });

export const getLineage = (pid) => apiFetch(`${base}/${pid}/lineage`);
export const listRuns = (pid) => apiFetch(`${base}/${pid}/runs`);
export const getRun = (pid, runId) => apiFetch(`${base}/${pid}/runs/${runId}`);

export const listVersions = (pid) => apiFetch(`${base}/${pid}/versions`);
export const getVersion = (pid, vid) => apiFetch(`${base}/${pid}/versions/${vid}`);
export const getVersionDiff = (pid, vid, against = "active") => apiFetch(`${base}/${pid}/versions/${vid}/diff?against=${against}`);
export const restoreVersion = (pid, vid) => apiFetch(`${base}/${pid}/versions/${vid}/restore`, { method: "POST", body: { confirm: true } });

// Module 11 — publish a gold dataset into Superset.
export const publishDataset = (pid, did, payload = {}) => apiFetch(`${base}/${pid}/datasets/${did}/publish`, { method: "POST", body: payload });
export const getDatasetPublication = (pid, did) => apiFetch(`${base}/${pid}/datasets/${did}/publication`);
export const unpublishDataset = (pid, did) => apiFetch(`${base}/${pid}/datasets/${did}/publication`, { method: "DELETE" });
export const listPublications = (pid) => apiFetch(`${base}/${pid}/publications`);

// Module 12 — indicator suggestion (Mistral + heuristic fallback) & dashboard generation.
export const suggestIndicators = (pid, did, context) => apiFetch(`${base}/${pid}/datasets/${did}/suggest-indicators${context ? `?context=${encodeURIComponent(context)}` : ""}`, { method: "POST" });
export const generateDashboard = (pid, did, payload) => apiFetch(`${base}/${pid}/datasets/${did}/generate-dashboard`, { method: "POST", body: payload });
export const regenerateDashboard = (pid, did) => apiFetch(`${base}/${pid}/datasets/${did}/regenerate-dashboard`, { method: "POST" });
export const getDashboardStatus = (pid, did) => apiFetch(`${base}/${pid}/datasets/${did}/dashboard`);
export const deleteDashboard = (pid, did) => apiFetch(`${base}/${pid}/datasets/${did}/dashboard`, { method: "DELETE" });
export const listDashboards = (pid) => apiFetch(`${base}/${pid}/dashboards`);

// Module 9 — admin, read-only supervision across every engineer's projects.
const adminBase = "/api/medallion/admin";
export const getAdminOverview = () => apiFetch(`${adminBase}/overview`);
export const getAdminProjectDetail = (pid) => apiFetch(`${adminBase}/projects/${pid}`);
