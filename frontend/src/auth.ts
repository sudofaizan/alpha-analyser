// @ts-nocheck
/** Shared auth helpers for Alpha Analyser. */

export const AUTH_TOKEN_KEY = "analyser_auth_token";
export const AUTH_USER_KEY = "analyser_auth_user";

function defaultApiBase() {
  if (import.meta.env.VITE_ANALYSER_API) return import.meta.env.VITE_ANALYSER_API;
  if (import.meta.env.PROD && typeof window !== "undefined") return "";
  return "http://127.0.0.1:8090";
}

export function apiBase() {
  const s = String(defaultApiBase()).trim().replace(/\/$/, "");
  if (!s) return "";
  if (!/^https?:\/\//i.test(s)) {
    const proto = window.location.protocol === "https:" ? "https:" : "http:";
    return `${proto}//${s}`.replace(/\/$/, "");
  }
  return s;
}

export function apiUrl(path) {
  const base = apiBase();
  return base ? `${base}${path}` : path;
}

export function getToken() {
  return localStorage.getItem(AUTH_TOKEN_KEY) || "";
}

export function getStoredUser() {
  try {
    return JSON.parse(localStorage.getItem(AUTH_USER_KEY) || "null");
  } catch (_) {
    return null;
  }
}

export function saveSession(token, user) {
  localStorage.setItem(AUTH_TOKEN_KEY, token);
  localStorage.setItem(AUTH_USER_KEY, JSON.stringify(user || null));
}

export function clearSession() {
  localStorage.removeItem(AUTH_TOKEN_KEY);
  localStorage.removeItem(AUTH_USER_KEY);
}

export function authHeaders(extra = {}) {
  const h = { "Content-Type": "application/json", ...extra };
  const token = getToken();
  if (token) h.Authorization = `Bearer ${token}`;
  return h;
}

export function requireAuthPage() {
  if (!getToken()) {
    window.location.href = "/login.html";
    return false;
  }
  return true;
}

export function redirectIfAuthed(target = "/index.html") {
  if (getToken()) {
    window.location.replace(target === "/" ? "/index.html" : target);
    return true;
  }
  return false;
}

export async function fetchMe() {
  const res = await fetch(apiUrl("/api/auth/me"), { headers: authHeaders() });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || !data.ok) {
    clearSession();
    return null;
  }
  saveSession(getToken(), data.user);
  return data.user;
}

export function accessMessage(reason) {
  if (reason === "not_allowed") {
    return "Your email is not approved yet. Contact admin to enable access.";
  }
  if (reason === "no_subscription") {
    return "No active subscription. Contact admin to set your expiry date.";
  }
  if (reason === "expired") {
    return "Subscription expired. Contact admin.";
  }
  return "Subscription inactive. Contact admin.";
}
