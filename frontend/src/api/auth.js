import { apiFetch } from "./client.js";

export const login = (email, password) =>
  apiFetch("/api/auth/login", { method: "POST", body: { email, password }, auth: false });

export const me = () => apiFetch("/api/auth/me");

export const forgotPassword = (email) =>
  apiFetch("/api/auth/forgot-password", { method: "POST", body: { email }, auth: false });

export const resetPassword = (token, newPassword) =>
  apiFetch("/api/auth/reset-password", { method: "POST", body: { token, new_password: newPassword }, auth: false });
