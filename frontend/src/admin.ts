// @ts-nocheck
import {
  accessMessage,
  apiUrl,
  authHeaders,
  clearSession,
  fetchMe,
  getStoredUser,
  requireAuthPage,
} from "./auth";
import { displaySignalId } from "./signal-history";

const usersBody = document.getElementById("usersBody");
const adminMsg = document.getElementById("adminMsg");
const trackStats = document.getElementById("trackStats");
const trackActiveBody = document.getElementById("trackActiveBody");
const trackClosedBody = document.getElementById("trackClosedBody");

function showAdminMsg(text, ok = false) {
  adminMsg.textContent = text;
  adminMsg.className = `auth-msg show ${ok ? "ok" : "err"}`;
}

function fmtDate(value) {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleDateString();
}

function statusBadge(user) {
  if (user.is_admin) return '<span class="badge admin">Admin</span>';
  if (user.has_access) return '<span class="badge ok">Active</span>';
  return `<span class="badge no">${accessMessage(user.access_reason)}</span>`;
}

function tgBadge(user) {
  const tg = user.telegram || {};
  if (!tg.enabled) return '<span class="badge no">TG off</span>';
  if (tg.inChannel) return `<span class="badge ok">@${tg.username || "?"}</span>`;
  if (tg.username) return `<span class="badge admin">@${tg.username}</span>`;
  return "—";
}

async function loadTracking() {
  if (!trackStats) return;
  const res = await fetch(apiUrl("/api/admin/tracking?recent=20"), { headers: authHeaders() });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || !data.ok) throw new Error(data.error || "Tracking load failed");
  const eng = data.engine || {};
  const st = data.stats || {};
  trackStats.innerHTML = `
    <span class="badge ${eng.running ? "ok" : "no"}">${eng.running ? "Engine running" : "Engine stopped"}</span>
    Active: <b>${st.active ?? 0}</b> · Pending fill: ${st.pendingFill ?? 0} · Runners: ${st.runnerPhase ?? 0}
    · Wins: ${st.wins ?? 0} · Losses: ${st.losses ?? 0}
    · Last tick: ${eng.lastTickAt || "—"}
    ${eng.lastTickError ? `<span class="badge no">${eng.lastTickError}</span>` : ""}
  `;
  const active = data.activeSignals || [];
  trackActiveBody.innerHTML = active.length
    ? active.map((s) => `<tr>
        <td class="hist-id">${displaySignalId(s)}</td>
        <td>${s.userEmail || "—"}</td>
        <td>${s.symbol}</td>
        <td><span class="badge admin">${s.phase || "—"}</span></td>
        <td>${s.action}</td>
        <td>${s.tracking?.fillStatus || "—"}</td>
        <td>${(s.tracking?.timeline || []).slice(-2).map((e) => e.event).join(" → ") || "—"}</td>
      </tr>`).join("")
    : `<tr><td colspan="7">No active virtual signals</td></tr>`;
  const closed = data.recentClosed || [];
  trackClosedBody.innerHTML = closed.length
    ? closed.map((s) => `<tr>
        <td class="hist-id">${displaySignalId(s)}</td>
        <td>${s.userEmail || "—"}</td>
        <td><span class="badge ${s.outcome === "WIN" ? "ok" : s.outcome === "LOSS" ? "no" : "admin"}">${s.outcome || s.status}</span></td>
        <td>${s.symbol}</td>
        <td>${s.closedAt ? new Date(s.closedAt).toLocaleString() : "—"}</td>
      </tr>`).join("")
    : `<tr><td colspan="5">No closed signals yet</td></tr>`;
}

async function removeTelegram(userId) {
  if (!window.confirm("Remove user from Telegram signal channel?")) return;
  const res = await fetch(apiUrl(`/api/admin/users/${userId}/telegram/remove`), {
    method: "POST",
    headers: authHeaders(),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || !data.ok) throw new Error(data.error || "Telegram remove failed");
  showAdminMsg(data.message || "Removed from Telegram", true);
  await loadUsers();
}

async function loadUsers() {
  const res = await fetch(apiUrl("/api/admin/users"), { headers: authHeaders() });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || !data.ok) throw new Error(data.error || "Failed to load users");
  usersBody.innerHTML = data.users.map((u) => {
    const expiryVal = u.subscription_expires_at
      ? String(u.subscription_expires_at).slice(0, 10)
      : "";
    const allowedChecked = u.email_allowed ? "checked" : "";
    const adminRow = u.is_admin
      ? `<span class="badge admin">admin</span>`
      : `<div class="row-actions">
          <label><input type="checkbox" data-allow="${u.id}" ${allowedChecked} /> Allow</label>
          <input type="date" data-expiry="${u.id}" value="${expiryVal}" />
          <button type="button" data-save="${u.id}">Save</button>
          ${u.telegram?.username ? `<button type="button" data-tg-remove="${u.id}">Remove TG</button>` : ""}
          <button type="button" data-del="${u.id}">Delete</button>
        </div>`;
    return `<tr>
      <td>${u.email}</td>
      <td>${u.is_admin ? "Admin" : "User"}</td>
      <td>${tgBadge(u)}</td>
      <td>${u.email_allowed ? "Yes" : "No"}</td>
      <td>${fmtDate(u.subscription_expires_at)}</td>
      <td>${statusBadge(u)}</td>
      <td>${adminRow}</td>
    </tr>`;
  }).join("") || `<tr><td colspan="7">No users</td></tr>`;

  usersBody.querySelectorAll("[data-save]").forEach((btn) => {
    btn.addEventListener("click", () => saveUser(Number(btn.getAttribute("data-save"))));
  });
  usersBody.querySelectorAll("[data-del]").forEach((btn) => {
    btn.addEventListener("click", () => deleteUser(Number(btn.getAttribute("data-del"))));
  });
  usersBody.querySelectorAll("[data-tg-remove]").forEach((btn) => {
    btn.addEventListener("click", () => removeTelegram(Number(btn.getAttribute("data-tg-remove"))).catch((e) => showAdminMsg(e.message)));
  });
}

async function saveUser(userId) {
  const allowEl = usersBody.querySelector(`[data-allow="${userId}"]`);
  const expiryEl = usersBody.querySelector(`[data-expiry="${userId}"]`);
  const body = {
    email_allowed: allowEl?.checked ?? false,
    subscription_expires_at: expiryEl?.value ? `${expiryEl.value}T23:59:59Z` : null,
  };
  const res = await fetch(apiUrl(`/api/admin/users/${userId}`), {
    method: "PATCH",
    headers: authHeaders(),
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || !data.ok) throw new Error(data.error || "Update failed");
  showAdminMsg("User updated", true);
  await loadUsers();
}

async function deleteUser(userId) {
  if (!window.confirm("Delete this user?")) return;
  const res = await fetch(apiUrl(`/api/admin/users/${userId}`), {
    method: "DELETE",
    headers: authHeaders(),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || !data.ok) throw new Error(data.error || "Delete failed");
  showAdminMsg("User deleted", true);
  await loadUsers();
}

async function addUser() {
  const email = document.getElementById("newEmail").value.trim();
  const expiry = document.getElementById("newExpiry").value;
  const password = document.getElementById("newPassword").value.trim() || "changeme123";
  const email_allowed = document.getElementById("newAllowed").checked;
  if (!email) {
    showAdminMsg("Email required");
    return;
  }
  const res = await fetch(apiUrl("/api/admin/users"), {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify({
      email,
      password,
      email_allowed,
      subscription_expires_at: expiry ? `${expiry}T23:59:59Z` : null,
    }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || !data.ok) throw new Error(data.error || "Create failed");
  showAdminMsg(`User ${email} saved`, true);
  document.getElementById("newEmail").value = "";
  document.getElementById("newPassword").value = "";
  await loadUsers();
}

async function boot() {
  if (!requireAuthPage()) return;
  let user = getStoredUser();
  user = (await fetchMe()) || user;
  if (!user) {
    window.location.href = "/login.html";
    return;
  }
  if (!user.is_admin) {
    window.location.replace("/index.html");
    return;
  }
  document.getElementById("btnDashboard").addEventListener("click", () => {
    window.location.replace("/index.html");
  });
  document.getElementById("btnLogout").addEventListener("click", () => {
    clearSession();
    window.location.href = "/login.html";
  });
  document.getElementById("btnAddUser").addEventListener("click", () => {
    addUser().catch((e) => showAdminMsg(e.message));
  });
  document.getElementById("btnRefreshTracking")?.addEventListener("click", () => {
    loadTracking().catch((e) => showAdminMsg(e.message));
  });
  try {
    await Promise.all([loadUsers(), loadTracking()]);
    setInterval(() => loadTracking().catch(() => {}), 30000);
  } catch (e) {
    showAdminMsg(e.message);
  }
}

boot();
