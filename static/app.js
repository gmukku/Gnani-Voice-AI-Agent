/* Live dashboard updates over WebSocket (bonus requirement).
 *
 * The page renders server-side; this only reacts to change events. On a
 * call.completed event the affected row is re-fetched and swapped in place, so
 * a disposition appears the moment the webhook lands without a full reload.
 */

(function () {
  "use strict";

  var statusEl = document.getElementById("ws-status");
  var labelEl = document.getElementById("ws-label");
  var socket = null;
  var retryDelay = 1000;
  var keepAlive = null;

  function setStatus(state, text) {
    if (!statusEl) return;
    statusEl.className = "pill pill-" + state;
    if (labelEl) labelEl.textContent = text;
  }

  function connect() {
    var scheme = location.protocol === "https:" ? "wss:" : "ws:";
    socket = new WebSocket(scheme + "//" + location.host + "/ws/calls");

    socket.onopen = function () {
      setStatus("live", "live");
      retryDelay = 1000;
      // The server reads to detect disconnects; a periodic ping keeps
      // intermediaries from closing an idle socket.
      keepAlive = setInterval(function () {
        if (socket && socket.readyState === WebSocket.OPEN) socket.send("ping");
      }, 25000);
    };

    socket.onmessage = function (event) {
      var message;
      try {
        message = JSON.parse(event.data);
      } catch (err) {
        return;
      }
      handle(message);
    };

    socket.onclose = function () {
      setStatus("down", "reconnecting");
      if (keepAlive) clearInterval(keepAlive);
      // Back off so a server restart does not produce a reconnect storm.
      setTimeout(connect, retryDelay);
      retryDelay = Math.min(retryDelay * 2, 15000);
    };

    socket.onerror = function () {
      setStatus("down", "offline");
    };
  }

  function handle(message) {
    var data = message.data || {};
    if (!data.call_id) return;

    if (message.event === "call.created") {
      // A brand-new row cannot be patched in isolation; reload once so the
      // summary cards and chart stay consistent with the table.
      scheduleReload();
      return;
    }
    refreshRow(data.call_id);
  }

  var reloadTimer = null;
  function scheduleReload() {
    // Coalesce bursts (e.g. a CSV upload) into a single reload.
    if (reloadTimer) clearTimeout(reloadTimer);
    reloadTimer = setTimeout(function () {
      location.reload();
    }, 1200);
  }

  function refreshRow(callId) {
    var row = document.querySelector('tr[data-call-id="' + callId + '"]');
    if (!row) {
      scheduleReload();
      return;
    }

    fetch("/api/v1/calls/" + encodeURIComponent(callId), {
      headers: { Accept: "application/json" }
    })
      .then(function (res) {
        return res.ok ? res.json() : null;
      })
      .then(function (call) {
        if (!call) return;
        patch(row, call);
        row.classList.remove("flash");
        void row.offsetWidth; // restart the animation
        row.classList.add("flash");
        // Counts and chart change with a disposition; refresh them too.
        scheduleReload();
      })
      .catch(function () {});
  }

  function patch(row, call) {
    var cells = row.cells;
    if (cells.length < 12) return;

    cells[6].innerHTML = tag("status-", call.call_status);
    cells[7].textContent = duration(call.call_duration_seconds);
    cells[8].innerHTML = call.stage_code
      ? tag("stage stage-", call.stage_code)
      : '<span class="dim">—</span>';
    cells[9].textContent = call.disposition_reason || "—";
    cells[9].title = call.disposition_reason || "";
    cells[10].textContent = dateOnly(call.ptp_date);
    cells[11].textContent = call.language_captured || "—";
  }

  function tag(prefix, value) {
    var safe = String(value == null ? "" : value);
    var cls = prefix + safe.toLowerCase();
    return '<span class="tag ' + cls + '">' + escapeHtml(safe) + "</span>";
  }

  function duration(seconds) {
    if (seconds == null) return "—";
    var m = Math.floor(seconds / 60);
    var s = seconds % 60;
    return m > 0 ? m + "m " + s + "s" : s + "s";
  }

  function dateOnly(value) {
    if (!value) return "—";
    return String(value).slice(0, 10);
  }

  function escapeHtml(text) {
    var div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
  }

  if (window.WebSocket) {
    connect();
  } else {
    setStatus("down", "unsupported");
  }
})();
