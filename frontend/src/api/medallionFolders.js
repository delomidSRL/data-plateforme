import { apiFetch } from "./client.js";

// Module 15 — purely organizational, personal folders over "my" medallion projects. Governed
// end-to-end by ownership (Module 9); never touches build/run/lineage.
const base = "/api/medallion/folders";

export const listFolders = () => apiFetch(`${base}/`);
export const createFolder = (name) => apiFetch(`${base}/`, { method: "POST", body: { name } });
export const renameFolder = (fid, name) => apiFetch(`${base}/${fid}`, { method: "PATCH", body: { name } });
export const deleteFolder = (fid) => apiFetch(`${base}/${fid}`, { method: "DELETE" });
