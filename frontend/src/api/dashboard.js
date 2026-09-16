import { apiFetch } from "./client.js";

export const getOverview = () => apiFetch("/api/dashboard/overview");
