// @ts-nocheck
import {
  apiUrl,
  redirectIfAuthed,
  saveSession,
} from "./auth";

const form = document.getElementById("signupForm");
const msg = document.getElementById("msg");
const btn = document.getElementById("btnSubmit");

function showMsg(text, ok = false) {
  msg.textContent = text;
  msg.className = `auth-msg show ${ok ? "ok" : "err"}`;
}

if (redirectIfAuthed("/")) {
  // redirected
} else {
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const password = document.getElementById("password").value;
    const password2 = document.getElementById("password2").value;
    if (password !== password2) {
      showMsg("Passwords do not match");
      return;
    }
    btn.disabled = true;
    showMsg("Creating account…", true);
    try {
      const res = await fetch(apiUrl("/api/auth/signup"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email: document.getElementById("email").value.trim(),
          password,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok) throw new Error(data.error || "Signup failed");
      saveSession(data.token, data.user);
      window.location.replace("/index.html");
    } catch (err) {
      showMsg(err.message || "Signup failed");
      btn.disabled = false;
    }
  });
}
