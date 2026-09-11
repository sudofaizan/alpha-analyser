import { fetchMe, getToken } from "./auth";

async function boot() {
  if (!getToken()) {
    window.location.replace("/login.html");
    return;
  }
  const user = await fetchMe();
  if (!user) {
    window.location.replace("/login.html");
    return;
  }
  await import("./chart-bridge");
  const { startDashboard } = await import("./app-client");
  startDashboard(user);
}

boot();
