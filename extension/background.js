// Keeps the toolbar badge in sync with the render queue and lets other parts
// of the extension open/focus the dashboard.

const DEFAULT_URL = "http://127.0.0.1:8000";

async function dashboardUrl() {
  const { dashboardUrl } = await chrome.storage.sync.get("dashboardUrl");
  return dashboardUrl || DEFAULT_URL;
}

async function openDashboard() {
  const url = await dashboardUrl();
  const origin = new URL(url).origin;
  const tabs = await chrome.tabs.query({});
  const existing = tabs.find((t) => t.url && t.url.startsWith(origin));
  if (existing) {
    await chrome.tabs.update(existing.id, { active: true });
    await chrome.windows.update(existing.windowId, { focused: true });
  } else {
    await chrome.tabs.create({ url });
  }
}

async function refreshBadge() {
  try {
    const url = await dashboardUrl();
    const r = await fetch(`${url}/api/queue`, { cache: "no-store" });
    if (!r.ok) throw new Error("bad status");
    const q = await r.json();
    const n = q.in_flight + q.queued;
    await chrome.action.setBadgeText({ text: n ? String(n) : "" });
    await chrome.action.setBadgeBackgroundColor({
      color: q.paused ? "#8b90a0" : "#b46bff",
    });
    await chrome.action.setTitle({
      title: q.paused
        ? "Brainrotter — queue paused"
        : `Brainrotter — ${q.in_flight} rendering, ${q.queued} waiting`,
    });
  } catch {
    await chrome.action.setBadgeText({ text: "" });
    await chrome.action.setTitle({ title: "Brainrotter — dashboard offline" });
  }
}

chrome.runtime.onInstalled.addListener(() => {
  chrome.alarms.create("poll", { periodInMinutes: 0.25 });
  refreshBadge();
});
chrome.runtime.onStartup.addListener(refreshBadge);
chrome.alarms.onAlarm.addListener((a) => a.name === "poll" && refreshBadge());
chrome.runtime.onMessage.addListener((msg) => {
  if (msg === "open") openDashboard();
  if (msg === "refresh") refreshBadge();
});
