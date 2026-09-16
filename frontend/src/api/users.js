import { apiFetch } from "./client.js";

export const listUsers = () => apiFetch("/api/users/");

export const createUser = (payload) =>
  apiFetch("/api/users/", { method: "POST", body: payload });

export const updateUser = (id, payload) =>
  apiFetch(`/api/users/${id}`, { method: "PUT", body: payload });

export const deleteUser = (id) =>
  apiFetch(`/api/users/${id}`, { method: "DELETE" });
