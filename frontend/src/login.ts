// @ts-nocheck
import {
  apiUrl,
  redirectIfAuthed,
  saveSession,
} from "./auth";

const form = document.getElementById("loginForm");
const msg = document.getElementById("msg");
const btn = document.getElementById("btnSubmit");

function showMsg(text, ok = false) {
  msg.textContent = text;
  msg.className = `auth-msg show ${ok ? "ok" : "err"}`;
}

if (redirectIfAuthed("/index.html")) {
  // redirected
} else {
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    btn.disabled = true;
    showMsg("Signing in…", true);
    try {
      const res = await fetch(apiUrl("/api/auth/login"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email: document.getElementById("email").value.trim(),
          password: document.getElementById("password").value,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok) throw new Error(data.error || "Login failed");
      saveSession(data.token, data.user);
      window.location.replace("/index.html");
    } catch (err) {
      showMsg(err.message || "Login failed");
      btn.disabled = false;
    }
  });
}
