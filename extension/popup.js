const DEFAULT_URL = "http://127.0.0.1:8000";
const $ = (id) => document.getElementById(id);

async function base() {
  const { dashboardUrl } = await chrome.storage.sync.get("dashboardUrl");
  return dashboardUrl || DEFAULT_URL;
}

async function load() {
  const url = await base();
  try {
    const q = await (await fetch(`${url}/api/queue`, { cache: "no-store" })).json();
    ONLINE = true;
    $("status").textContent = q.paused ? "paused" : "running";
    $("status").className = q.paused ? "work" : "ok";
    $("inflight").textContent = q.in_flight;
    $("queued").textContent = q.queued;
    $("done").textContent = q.done;
    $("pause").textContent = q.paused ? "Resume" : "Pause";
    $("open").textContent = "Open dashboard";
    $("msg").textContent = "";
    return q;
  } catch {
    ONLINE = false;
    $("status").textContent = "offline";
    $("status").className = "off";
    $("msg").textContent = "Open the Brainrotter app to start it.";
    $("open").textContent = "Retry connection";
  }
}

async function post(path) {
  const url = await base();
  try {
    await fetch(`${url}${path}`, { method: "POST", cache: "no-store",
      headers: { "content-type": "application/json" }, body: "{}" });
    chrome.runtime.sendMessage("refresh");
    load();
  } catch {
    $("msg").textContent = "dashboard offline";
  }
}

let ONLINE = false;
$("open").addEventListener("click", () => {
  if (ONLINE) {
    chrome.runtime.sendMessage("open");
    window.close();
  } else {
    load();  // retry
  }
});
$("pause").addEventListener("click", async () => {
  const q = await load();
  post(q && q.paused ? "/api/queue/resume" : "/api/queue/pause");
});
$("clear").addEventListener("click", () => post("/api/queue/clear"));

load();
