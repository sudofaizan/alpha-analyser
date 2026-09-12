// @ts-nocheck
import {
  apiUrl,
  redirectIfAuthed,
  saveSession,
} from "./auth";

const form = document.getElementById("signupForm");
const msg = document.getElementById("msg");
const btn = document.getElementById("btnSubmit");
const planGrid = document.getElementById("planGrid");
const priceValue = document.getElementById("priceValue");
const referralInput = document.getElementById("referral");

let plans = [];
let selectedPlan = "weekly";
let quoteTimer = null;

function showMsg(text, ok = false) {
  msg.textContent = text;
  msg.className = `auth-msg show ${ok ? "ok" : "err"}`;
}

function formatPrice(n) {
  if (n <= 0) return "FREE";
  return `$${n.toFixed(2)} USDT`;
}

function renderPlans() {
  if (!planGrid) return;
  planGrid.innerHTML = plans
    .map(
      (p) => `
    <label class="plan-card ${p.id === selectedPlan ? "selected" : ""}">
      <input type="radio" name="plan" value="${p.id}" ${p.id === selectedPlan ? "checked" : ""} />
      <span class="plan-name">${p.label}</span>
      <span class="plan-price">$${p.price_usd} ${p.currency}</span>
    </label>
  `,
    )
    .join("");

  planGrid.querySelectorAll('input[name="plan"]').forEach((el) => {
    el.addEventListener("change", () => {
      selectedPlan = el.value;
      renderPlans();
      refreshQuote();
    });
  });
}

function updatePayButton(quote) {
  if (!btn) return;
  if (!quote) {
    btn.textContent = "Pay & create account";
    return;
  }
  if (quote.free) {
    btn.textContent = "Get free access & create account";
  } else {
    btn.textContent = `Pay ${formatPrice(quote.price_usd)} & create account`;
  }
}

async function refreshQuote() {
  if (quoteTimer) clearTimeout(quoteTimer);
  quoteTimer = setTimeout(async () => {
    const referral = referralInput?.value?.trim() || "";
    try {
      const res = await fetch(apiUrl("/api/auth/quote"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ plan_id: selectedPlan, referral_code: referral }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok) {
        if (priceValue) priceValue.textContent = "—";
        updatePayButton(null);
        if (referral) showMsg(data.error || "Invalid referral for this plan");
        return;
      }
      const q = data.quote;
      if (priceValue) {
        if (q.discount_percent > 0) {
          priceValue.innerHTML = `<s style="opacity:.5;margin-right:.35rem">${formatPrice(q.original_price_usd)}</s>${formatPrice(q.price_usd)}`;
        } else {
          priceValue.textContent = formatPrice(q.price_usd);
        }
      }
      updatePayButton(q);
      if (msg.classList.contains("err")) msg.className = "auth-msg";
    } catch (_) {
      if (priceValue) priceValue.textContent = "—";
    }
  }, 200);
}

async function loadPlans() {
  const res = await fetch(apiUrl("/api/auth/plans"));
  const data = await res.json().catch(() => ({}));
  if (!res.ok || !data.ok) throw new Error("Could not load plans");
  plans = data.plans || [];
  if (plans.length && !plans.find((p) => p.id === selectedPlan)) {
    selectedPlan = plans[0].id;
  }
  renderPlans();
  refreshQuote();
}

if (redirectIfAuthed("/")) {
  // redirected
} else {
  referralInput?.addEventListener("input", refreshQuote);

  loadPlans().catch((e) => showMsg(e.message || "Failed to load plans"));

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const password = document.getElementById("password").value;
    const password2 = document.getElementById("password2").value;
    if (password !== password2) {
      showMsg("Passwords do not match");
      return;
    }
    btn.disabled = true;
    showMsg("Processing…", true);
    try {
      const res = await fetch(apiUrl("/api/auth/signup"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email: document.getElementById("email").value.trim(),
          password,
          plan_id: selectedPlan,
          referral_code: referralInput?.value?.trim() || "",
          mock_pay: true,
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
