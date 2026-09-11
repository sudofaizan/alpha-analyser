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

const usersBody = document.getElementById("usersBody");
const adminMsg = document.getElementById("adminMsg");

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
          <button type="button" data-del="${u.id}">Delete</button>
        </div>`;
    return `<tr>
      <td>${u.email}</td>
      <td>${u.is_admin ? "Admin" : "User"}</td>
      <td>${u.email_allowed ? "Yes" : "No"}</td>
      <td>${fmtDate(u.subscription_expires_at)}</td>
      <td>${statusBadge(u)}</td>
      <td>${adminRow}</td>
    </tr>`;
  }).join("") || `<tr><td colspan="6">No users</td></tr>`;

  usersBody.querySelectorAll("[data-save]").forEach((btn) => {
    btn.addEventListener("click", () => saveUser(Number(btn.getAttribute("data-save"))));
  });
  usersBody.querySelectorAll("[data-del]").forEach((btn) => {
    btn.addEventListener("click", () => deleteUser(Number(btn.getAttribute("data-del"))));
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
  try {
    await loadUsers();
  } catch (e) {
    showAdminMsg(e.message);
  }
}

boot();
