"""Minimal operator console UI."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["operator-ui"])


@router.get("/operator/ui", response_class=HTMLResponse)
def operator_ui() -> HTMLResponse:
    """Serve the minimal operator console."""

    return HTMLResponse(OPERATOR_UI_HTML)


OPERATOR_UI_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>M-Pesa MCP Operator Console</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f7f8fa;
      --panel: #ffffff;
      --text: #151922;
      --muted: #596273;
      --line: #dfe3ea;
      --accent: #127c56;
      --danger: #b42318;
      --warning: #b54708;
      --shadow: 0 8px 24px rgba(21, 25, 34, 0.08);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont,
        "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--text);
    }
    header {
      padding: 24px;
      border-bottom: 1px solid var(--line);
      background: var(--panel);
    }
    main {
      width: min(1180px, calc(100% - 32px));
      margin: 24px auto 48px;
      display: grid;
      gap: 16px;
    }
    h1, h2 {
      margin: 0;
      letter-spacing: 0;
    }
    h1 { font-size: 1.45rem; }
    h2 { font-size: 1rem; }
    p { margin: 6px 0 0; color: var(--muted); }
    .toolbar {
      display: grid;
      gap: 10px;
      grid-template-columns: 1fr auto auto;
      align-items: end;
    }
    label {
      display: grid;
      gap: 6px;
      font-size: 0.86rem;
      color: var(--muted);
    }
    input {
      min-height: 40px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 8px 10px;
      font: inherit;
    }
    button {
      min-height: 40px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 8px 12px;
      background: var(--panel);
      color: var(--text);
      font: inherit;
      cursor: pointer;
    }
    button.primary {
      background: var(--accent);
      color: #ffffff;
      border-color: var(--accent);
    }
    button.danger {
      color: var(--danger);
      border-color: #f2b8b5;
    }
    section {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
      overflow: hidden;
    }
    .section-head {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: center;
      padding: 14px 16px;
      border-bottom: 1px solid var(--line);
    }
    .content { padding: 16px; }
    .grid {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
    }
    .metric {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      background: #fbfcfd;
    }
    .metric span {
      display: block;
      color: var(--muted);
      font-size: 0.78rem;
    }
    .metric strong {
      display: block;
      margin-top: 4px;
      font-size: 1.35rem;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 0.9rem;
    }
    th, td {
      text-align: left;
      padding: 10px 8px;
      border-bottom: 1px solid var(--line);
      vertical-align: top;
    }
    th {
      color: var(--muted);
      font-weight: 600;
      background: #fbfcfd;
    }
    .actions {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }
    .status {
      min-height: 22px;
      color: var(--muted);
      font-size: 0.88rem;
    }
    .status.error { color: var(--danger); }
    .status.warn { color: var(--warning); }
    pre {
      margin: 0;
      white-space: pre-wrap;
      word-break: break-word;
      color: var(--muted);
      font-size: 0.84rem;
    }
    @media (max-width: 760px) {
      .toolbar { grid-template-columns: 1fr; }
      .grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      table { display: block; overflow-x: auto; }
    }
  </style>
</head>
<body>
  <header>
    <h1>M-Pesa MCP Operator Console</h1>
    <p>Minimal demo console for reviewing payments, approvals, audit events, and reconciliation.</p>
  </header>
  <main>
    <section aria-labelledby="auth-title">
      <div class="section-head">
        <h2 id="auth-title">Operator Access</h2>
        <div id="status" class="status">Paste an operator token, then refresh data.</div>
      </div>
      <div class="content toolbar">
        <label>
          Operator token
          <input id="operator-token" type="password" autocomplete="off" placeholder="Bearer token">
        </label>
        <button id="save-token">Save token</button>
        <button id="refresh" class="primary">Refresh data</button>
      </div>
    </section>

    <section aria-labelledby="analytics-title">
      <div class="section-head">
        <h2 id="analytics-title">Today Analytics Summary</h2>
      </div>
      <div class="content grid" id="analytics-summary"></div>
    </section>

    <section aria-labelledby="transactions-title">
      <div class="section-head">
        <h2 id="transactions-title">Recent Transactions</h2>
      </div>
      <div class="content" id="transactions"></div>
    </section>

    <section aria-labelledby="approvals-title">
      <div class="section-head">
        <h2 id="approvals-title">Pending Approvals</h2>
      </div>
      <div class="content" id="approvals"></div>
    </section>

    <section aria-labelledby="audit-title">
      <div class="section-head">
        <h2 id="audit-title">Recent Audit Events</h2>
      </div>
      <div class="content" id="audit-events"></div>
    </section>

    <section aria-labelledby="reconciliation-title">
      <div class="section-head">
        <h2 id="reconciliation-title">Reconciliation</h2>
        <button id="run-reconciliation" class="primary">Run reconciliation</button>
      </div>
      <div class="content"><pre id="reconciliation-output">No reconciliation run yet.</pre></div>
    </section>
  </main>

  <script>
    const tokenInput = document.querySelector("#operator-token");
    const statusBox = document.querySelector("#status");
    tokenInput.value = localStorage.getItem("operatorToken") || "";

    document.querySelector("#save-token").addEventListener("click", () => {
      localStorage.setItem("operatorToken", tokenInput.value.trim());
      setStatus("Token saved in this browser.");
    });
    document.querySelector("#refresh").addEventListener("click", refreshAll);
    document.querySelector("#run-reconciliation").addEventListener("click", runReconciliation);

    function authHeaders() {
      const token = tokenInput.value.trim() || localStorage.getItem("operatorToken") || "";
      return token ? { "Authorization": `Bearer ${token}` } : {};
    }

    async function api(path, options = {}) {
      const response = await fetch(path, {
        ...options,
        headers: { ...authHeaders(), ...(options.headers || {}) }
      });
      const body = await response.json();
      if (!response.ok) {
        throw new Error(body.detail || body.reason || `Request failed: ${response.status}`);
      }
      return body;
    }

    async function refreshAll() {
      try {
        setStatus("Loading operator data...");
        const [analytics, transactions, approvals, auditEvents] = await Promise.all([
          api("/operator/analytics/today"),
          api("/operator/transactions"),
          api("/approvals/pending"),
          api("/operator/audit-events")
        ]);
        renderAnalytics(analytics.summary);
        renderTransactions(transactions.transactions);
        renderApprovals(approvals.approvals);
        renderAuditEvents(auditEvents.audit_events);
        setStatus("Operator data refreshed.");
      } catch (error) {
        setStatus(error.message, "error");
      }
    }

    async function runReconciliation() {
      try {
        setStatus("Running reconciliation...");
        const result = await api("/operator/reconciliation/run", { method: "POST" });
        document.querySelector("#reconciliation-output").textContent =
          JSON.stringify(result.summary, null, 2);
        setStatus("Reconciliation completed.");
      } catch (error) {
        setStatus(error.message, "error");
      }
    }

    async function decideApproval(approvalId, action) {
      try {
        setStatus(`${action} approval ${approvalId}...`);
        await api(`/approvals/${approvalId}/${action}`, { method: "POST" });
        await refreshAll();
      } catch (error) {
        setStatus(error.message, "error");
      }
    }

    function renderAnalytics(summary) {
      const metrics = [
        ["Total", summary.total_transactions],
        ["Completed", summary.completed_transactions],
        ["Failed", summary.failed_transactions],
        ["Pending", summary.pending_transactions],
        ["Revenue", summary.total_revenue]
      ];
      document.querySelector("#analytics-summary").innerHTML = metrics.map(([label, value]) => `
        <div class="metric">
          <span>${escapeHtml(label)}</span>
          <strong>${escapeHtml(value)}</strong>
        </div>
      `).join("");
    }

    function renderTransactions(transactions) {
      document.querySelector("#transactions").innerHTML = table(
        ["Provider", "Rail", "Status", "Amount", "Phone", "Created"],
        transactions.map((item) => [
          item.provider, item.rail, item.status, item.amount, item.phone_number, item.created_at
        ])
      );
    }

    function renderApprovals(approvals) {
      if (!approvals.length) {
        document.querySelector("#approvals").innerHTML = "<p>No pending approvals.</p>";
        return;
      }
      const rows = approvals.map((item) => `
        <tr>
          <td>${escapeHtml(item.approval_id)}</td>
          <td>${escapeHtml(item.action)}</td>
          <td>${escapeHtml(item.reason)}</td>
          <td>${escapeHtml(item.created_at)}</td>
          <td class="actions">
            <button class="primary" data-approve="${escapeHtml(item.approval_id)}">Approve</button>
            <button class="danger" data-reject="${escapeHtml(item.approval_id)}">Reject</button>
          </td>
        </tr>
      `).join("");
      document.querySelector("#approvals").innerHTML = `
        <table>
          <thead><tr><th>ID</th><th>Action</th><th>Reason</th><th>Created</th><th>Decision</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      `;
      document.querySelectorAll("[data-approve]").forEach((button) => {
        button.addEventListener("click", () => decideApproval(button.dataset.approve, "approve"));
      });
      document.querySelectorAll("[data-reject]").forEach((button) => {
        button.addEventListener("click", () => decideApproval(button.dataset.reject, "reject"));
      });
    }

    function renderAuditEvents(events) {
      document.querySelector("#audit-events").innerHTML = table(
        ["Event", "Actor", "Correlation", "Created"],
        events.map((item) => [
          item.event_type, item.actor || "", item.correlation_id || "", item.created_at
        ])
      );
    }

    function table(headers, rows) {
      if (!rows.length) {
        return "<p>No records yet.</p>";
      }
      const headerCells = headers
        .map((header) => `<th>${escapeHtml(header)}</th>`)
        .join("");
      const bodyRows = rows
        .map((row) => `<tr>${
          row.map((cell) => `<td>${escapeHtml(cell)}</td>`).join("")
        }</tr>`)
        .join("");
      return `
        <table>
          <thead><tr>${headerCells}</tr></thead>
          <tbody>
            ${bodyRows}
          </tbody>
        </table>
      `;
    }

    function setStatus(message, tone = "") {
      statusBox.textContent = message;
      statusBox.className = tone ? `status ${tone}` : "status";
    }

    function escapeHtml(value) {
      return String(value ?? "").replace(/[&<>"']/g, (char) => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#039;"
      }[char]));
    }
  </script>
</body>
</html>
"""
