import { apiFetch } from "./client.js";

const base = "/api/superset-instances";

export const listInstances = () => apiFetch(`${base}/`);
export const getInstance = (iid) => apiFetch(`${base}/${iid}`);
export const testInstance = (iid) => apiFetch(`${base}/${iid}/test`, { method: "POST" });
