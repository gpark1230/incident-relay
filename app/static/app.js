const STATUS_LABELS = {
  sent: "Sent",
  failed: "Failed",
  pending: "Pending",
  rate_limited: "Rate limited",
};

const EVENT_LABELS = {
  "incident.created": "Incident created",
  "incident.updated": "Incident updated",
  "incident.commented": "New comment",
};

function fmtTime(iso) {
  const d = new Date(iso);
  return d.toLocaleString(undefined, {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

async function loadHealth() {
  const overallEl = document.getElementById("health-overall");
  const dbEl = document.getElementById("health-db");
  const redisEl = document.getElementById("health-redis");

  try {
    const res = await fetch("/health");
    const data = await res.json();

    const overallOk = data.status === "ok";
    overallEl.innerHTML = `<span class="dot ${overallOk ? "dot-ok" : "dot-bad"}"></span>${overallOk ? "Healthy" : "Degraded"}`;

    const dbOk = data.checks?.database === "ok";
    dbEl.innerHTML = `<span class="dot ${dbOk ? "dot-ok" : "dot-bad"}"></span>${dbOk ? "Connected" : "Error"}`;

    const redisOk = data.checks?.redis === "ok";
    redisEl.innerHTML = `<span class="dot ${redisOk ? "dot-ok" : "dot-bad"}"></span>${redisOk ? "Connected" : "Error"}`;
  } catch (err) {
    overallEl.innerHTML = `<span class="dot dot-bad"></span>Unreachable`;
    dbEl.textContent = "—";
    redisEl.textContent = "—";
  }
}

async function loadNotifications() {
  const listEl = document.getElementById("notifications-list");
  const statusFilter = document.getElementById("filter-status").value;

  const params = new URLSearchParams();
  if (statusFilter) params.set("status", statusFilter);

  try {
    const res = await fetch(`/notifications?${params.toString()}`);
    const items = await res.json();

    if (!items.length) {
      listEl.innerHTML = `<div class="empty-state">No notifications yet — push an event through the Redis queue to see one land here.</div>`;
      return;
    }

    listEl.innerHTML = items.map((n) => `
      <div class="notification-card">
        <div class="main">
          <div class="event">${EVENT_LABELS[n.event] || n.event} — incident #${n.incident_id}</div>
          <div class="meta">
            to ${n.recipient} &middot; ${fmtTime(n.created_at)}
            ${n.error_message ? `&middot; ${n.error_message}` : ""}
          </div>
        </div>
        <div class="badges">
          ${n.retry_count > 0 ? `<span class="retry-note">${n.retry_count} ${n.retry_count === 1 ? "retry" : "retries"}</span>` : ""}
          <span class="badge badge-status-${n.status}">${STATUS_LABELS[n.status] || n.status}</span>
        </div>
      </div>
    `).join("");
  } catch (err) {
    listEl.innerHTML = `<div class="empty-state">Couldn't load notification history.</div>`;
  }
}

function updateRefreshNote() {
  document.getElementById("refresh-note").textContent =
    `Last updated ${new Date().toLocaleTimeString()}`;
}

async function refreshAll() {
  await Promise.all([loadHealth(), loadNotifications()]);
  updateRefreshNote();
}

document.getElementById("filter-status").addEventListener("change", loadNotifications);

refreshAll();
setInterval(refreshAll, 15000);
