// @ts-nocheck
/** Telegram channel linking — backend only, no browser Telegram API. */

import { apiUrl, authHeaders } from "./auth";

export function telegramStatusLabel(tg) {
  if (!tg?.enabled) return "Telegram not configured on server";
  const map = {
    none: "Not connected",
    awaiting_invite: "Username saved — click Add to signal channel",
    pending_bot: "Waiting — open bot link below and tap Start",
    linked: "Linked — sending invite…",
    in_channel: "In signal channel",
    invite_sent: "Tap the channel link below to join",
    disabled: "Telegram disabled",
  };
  return map[tg.status] || tg.status || "—";
}

export async function fetchTelegramStatus() {
  const res = await fetch(apiUrl("/api/telegram/status"), { headers: authHeaders() });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || !data.ok) throw new Error(data.error || "Telegram status failed");
  return data;
}

export async function connectTelegram(username) {
  const res = await fetch(apiUrl("/api/telegram/connect"), {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify({ username: username || "" }),
  });
  const data = await res.json().catch(() => ({}));
  return { ok: res.ok && data.ok, data, error: data.error || data.message };
}

export async function disconnectTelegram() {
  const res = await fetch(apiUrl("/api/telegram/disconnect"), {
    method: "POST",
    headers: authHeaders(),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || !data.ok) throw new Error(data.error || "Disconnect failed");
  return data;
}
