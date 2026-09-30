// window.__API_BASE_URL__ is written at container start (docker/runtime-config.sh) so one
// built image can be deployed against any backend URL without a rebuild.
const BASE_URL = window.__API_BASE_URL__ || import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

export class ApiError extends Error {
  constructor(message, status) {
    // `Error`'s own constructor coerces a non-string argument via ToString (an array becomes
    // "[object Object],[object Object]", a plain object becomes "[object Object]") — silently
    // destroying a structured FastAPI `detail` payload (a 422's list of jinja-guard
    // violations, a 409's {message, dataset_name}/{message, impact}, …). `.detail` below is
    // what every caller that expects one of those shapes must read instead of `.message`.
    super(typeof message === "string" ? message : (message?.message || "Une erreur est survenue."));
    this.status = status;
    this.detail = message;
  }
}

let authToken = null;
export function setAuthToken(token) {
  authToken = token;
}

// Annexe "élargir le scope de l'assistant IA" §debug — any endpoint that can trigger the AI
// attaches its captured prompt(s)/raw response(s) as `ai_debug` (see backend
// app/services/ai_client.py's reset_debug_log/get_debug_log). Checked here, once, in the
// single fetch choke point — every such endpoint gets DevTools console visibility for free,
// no per-call-site wiring needed.
function logAiDebug(data) {
  const entries = data?.ai_debug;
  if (!entries || entries.length === 0) return;
  entries.forEach((entry, i) => {
    console.groupCollapsed(`%cIA — appel ${i + 1}/${entries.length}`, "color:#8a5cf6;font-weight:600");
    console.log("Prompt :", entry.prompt);
    if (entry.error) console.error("Erreur :", entry.error, entry.response ? { reponse_brute: entry.response } : undefined);
    else console.log("Réponse brute :", entry.response);
    console.groupEnd();
  });
}

export async function apiFetch(path, { method = "GET", body, auth = true } = {}) {
  const headers = { "Content-Type": "application/json" };
  if (auth && authToken) headers.Authorization = `Bearer ${authToken}`;

  const response = await fetch(`${BASE_URL}${path}`, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });

  if (response.status === 204) return null;

  let data = null;
  try {
    data = await response.json();
  } catch {
    data = null;
  }

  if (!response.ok) {
    const message = data?.detail || "Une erreur est survenue.";
    throw new ApiError(message, response.status);
  }

  logAiDebug(data);
  return data;
}

// Binary download — carries the bearer token (auth is in-memory, not a cookie, so a plain
// <a href> can't reach an authenticated endpoint). Returns a Blob the caller hands to
// utils/download.saveBlob. The response body streams from the server; the browser buffers
// it on the client's own machine (the "no buffering" invariant is a control-plane concern).
export async function apiDownload(path) {
  const headers = {};
  if (authToken) headers.Authorization = `Bearer ${authToken}`;

  const response = await fetch(`${BASE_URL}${path}`, { headers });
  if (!response.ok) {
    let data = null;
    try { data = await response.json(); } catch { data = null; }
    throw new ApiError(data?.detail || "Une erreur est survenue.", response.status);
  }
  return response.blob();
}

// Multipart upload — no Content-Type header (the browser sets the multipart boundary),
// body is a FormData the caller builds (file + form fields).
export async function apiUpload(path, formData, { method = "POST" } = {}) {
  const headers = {};
  if (authToken) headers.Authorization = `Bearer ${authToken}`;

  const response = await fetch(`${BASE_URL}${path}`, { method, headers, body: formData });

  if (response.status === 204) return null;

  let data = null;
  try {
    data = await response.json();
  } catch {
    data = null;
  }

  if (!response.ok) {
    const message = data?.detail || "Une erreur est survenue.";
    throw new ApiError(message, response.status);
  }

  return data;
}
