import { apiFetch, apiUpload } from "./client.js";

const base = "/api/imports";

export const listImports = () => apiFetch(`${base}/`);
export const getImport = (id) => apiFetch(`${base}/${id}`);
export const updateImport = (id, payload) => apiFetch(`${base}/${id}`, { method: "PUT", body: payload });
export const triggerRun = (id) => apiFetch(`${base}/${id}/run`, { method: "POST" });
export const getImportStatus = (id) => apiFetch(`${base}/${id}/status`);
export const deleteImport = (id, dropTable = false) => apiFetch(`${base}/${id}?drop_table=${dropTable}`, { method: "DELETE" });

export const createImport = (file, { format, formatOptions = {}, targetSourceId, archiveSourceId, name, importMode = "typed", writeMode = "create" }) => {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("format", format);
  formData.append("format_options", JSON.stringify(formatOptions));
  formData.append("target_source_id", String(targetSourceId));
  formData.append("archive_source_id", String(archiveSourceId));
  if (name) formData.append("name", name);
  formData.append("import_mode", importMode);
  formData.append("write_mode", writeMode);
  return apiUpload(`${base}/`, formData);
};

export const reimport = (id, file) => {
  const formData = new FormData();
  formData.append("file", file);
  return apiUpload(`${base}/${id}/reimport`, formData);
};

export const getXmlCandidates = (file) => {
  const formData = new FormData();
  formData.append("file", file);
  return apiUpload(`${base}/xml-candidates`, formData);
};

export const getColumns = (file, format, formatOptions = {}) => {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("format", format);
  formData.append("format_options", JSON.stringify(formatOptions));
  return apiUpload(`${base}/columns`, formData);
};
