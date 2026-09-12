// @ts-nocheck
/** Virtual signal history + market status display helpers. */

import { authHeaders } from "./auth";

export function outcomeLabel(outcome, status) {
  if (outcome === "WIN") return "WIN";
  if (outcome === "LOSS") return "LOSS";
  if (outcome === "REMOVED" || status === "REMOVED") return "REMOVED";
  if (status === "ACTIVE") return "ACTIVE";
  return outcome || status || "—";
}

export function outcomeClass(outcome, status) {
  if (outcome === "WIN") return "win";
  if (outcome === "LOSS") return "loss";
  if (outcome === "REMOVED" || status === "REMOVED") return "removed";
  if (status === "ACTIVE") return "active";
  return "neutral";
}

export function marketStatusLabel(status) {
  if (status === "OPEN") return "Market Open";
  if (status === "CLOSED") return "Market Closed";
  return "Market —";
}

export function marketStatusClass(status) {
  if (status === "OPEN") return "open";
  if (status === "CLOSED") return "closed";
  return "unknown";
}

export function fmtSignalTime(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch (_) {
    return iso;
  }
}

export function displaySignalId(signal) {
  if (!signal) return "—";
  return signal.signalId || (signal.id != null ? `SIG-${signal.id}` : "—");
}

export function fmtPrice(p) {
  if (p == null || Number.isNaN(Number(p))) return "—";
  const n = Number(p);
  if (Math.abs(n) >= 100) return n.toFixed(2);
  if (Math.abs(n) < 10) return n.toFixed(5);
  return n.toFixed(3);
}

export async function fetchSignalHistory(apiUrlFn, symbol, limit = 50) {
  const sym = symbol ? `&symbol=${encodeURIComponent(symbol)}` : "";
  const res = await fetch(apiUrlFn(`/api/signals/history?limit=${limit}${sym}`), {
    headers: authHeaders(),
  });
  const data = await res.json();
  if (!res.ok || !data.ok) throw new Error(data.error || "Failed to load signal history");
  return data.signals || [];
}

export function renderHistoryTable(container, signals) {
  if (!container) return;
  if (!signals?.length) {
    container.innerHTML = '<p class="hist-empty">No virtual signals yet — load chart with trade signals enabled.</p>';
    return;
  }
  const rows = signals.map((s) => {
    const oc = outcomeClass(s.outcome, s.status);
    const label = outcomeLabel(s.outcome, s.status);
    const detail = s.statusDetail ? `<div class="hist-detail">${s.statusDetail}</div>` : "";
    const tp2 = s.tp2 != null ? fmtPrice(s.tp2) : "—";
    return `<tr class="hist-row ${oc}">
      <td class="hist-id">${displaySignalId(s)}</td>
      <td><span class="hist-outcome ${oc}">${label}</span>${detail}</td>
      <td>${s.symbol || "—"}</td>
      <td>${s.timeframe || "—"}</td>
      <td class="${s.action === "BUY" ? "buy" : "sell"}">${s.action || "—"}</td>
      <td>${s.orderType || "—"}</td>
      <td>${fmtPrice(s.entry)}</td>
      <td>${fmtPrice(s.sl)}</td>
      <td>${fmtPrice(s.tp1)}</td>
      <td>${tp2}</td>
      <td>${fmtSignalTime(s.createdAt)}</td>
      <td>${fmtSignalTime(s.closedAt)}</td>
    </tr>`;
  }).join("");
  container.innerHTML = `<table class="hist-table">
    <thead><tr>
      <th>Signal ID</th><th>Result</th><th>Symbol</th><th>TF</th><th>Side</th><th>Order</th>
      <th>Entry</th><th>SL</th><th>TP1</th><th>TP2</th><th>Created</th><th>Closed</th>
    </tr></thead>
    <tbody>${rows}</tbody>
  </table>`;
}

export function trackingStatusText(active) {
  if (!active) return null;
  if (active.tp1Hit) return "TP1 reached — tracking TP2 / trail";
  if (active.orderType === "LIMIT") return "Limit pending / tracking fill";
  return "Virtually tracking SL · TP1 · TP2";
}
