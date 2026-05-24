import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { clearToken, describeApiError, getStoredToken } from "../api/client";
import { AnalyticsCards } from "../components/AnalyticsCards";
import { ApprovalsPanel } from "../components/ApprovalsPanel";
import { AuditEvents, CallbackTimeline } from "../components/AuditEvents";
import { DashboardLayout } from "../components/DashboardLayout";
import { ErrorBanner } from "../components/ErrorBanner";
import { LoginScreen } from "../components/LoginScreen";
import { ReconciliationPanel } from "../components/ReconciliationPanel";
import { SystemStatusPanel } from "../components/SystemStatusPanel";
import { TransactionsTable } from "../components/TransactionsTable";
import { ReceiptLookupPanel } from "../features/receipts/ReceiptLookupPanel";
import { useOperatorData } from "../hooks/useOperatorData";

export function DashboardPage() {
  const [isAuthenticated, setIsAuthenticated] = useState(() => Boolean(getStoredToken()));
  const queryClient = useQueryClient();
  const data = useOperatorData(isAuthenticated);

  if (!isAuthenticated) {
    return <LoginScreen onAuthenticated={() => setIsAuthenticated(true)} />;
  }

  const dashboardError =
    data.analytics.error ??
    data.transactions.error ??
    data.approvals.error ??
    data.auditEvents.error ??
    data.reconciliation.error;

  async function refreshAll() {
    await queryClient.invalidateQueries();
  }

  function logout() {
    clearToken();
    setIsAuthenticated(false);
    queryClient.clear();
  }

  return (
    <DashboardLayout
      isRefreshing={data.transactions.isFetching || data.analytics.isFetching}
      onLogout={logout}
      onRefresh={refreshAll}
    >
      {dashboardError ? <ErrorBanner message={describeApiError(dashboardError)} /> : null}

      <AnalyticsCards
        approvals={data.approvals.data}
        isLoading={data.analytics.isLoading || data.approvals.isLoading}
        reconciliation={data.reconciliation.data}
        summary={data.analytics.data}
        system={data.health.data}
      />

      <div className="grid gap-5 xl:grid-cols-[1.35fr_0.65fr]">
        <section className="panel overflow-hidden" id="transactions">
          <div className="panel-header">
            <div>
              <h2 className="text-base font-semibold">Recent Transactions</h2>
              <p className="mt-1 text-sm text-muted">Provider-aware payment records.</p>
            </div>
          </div>
          <div className="p-0">
            <TransactionsTable
              isLoading={data.transactions.isLoading}
              transactions={data.transactions.data}
            />
          </div>
        </section>

        <section className="panel">
          <div className="panel-header">
            <div>
              <h2 className="text-base font-semibold">System Status</h2>
              <p className="mt-1 text-sm text-muted">Backend health and storage mode.</p>
            </div>
          </div>
          <div className="p-5">
            <SystemStatusPanel isLoading={data.health.isLoading} status={data.health.data} />
          </div>
        </section>
      </div>

      <div className="grid gap-5 xl:grid-cols-2">
        <section className="panel" id="approvals">
          <div className="panel-header">
            <div>
              <h2 className="text-base font-semibold">Pending Approvals</h2>
              <p className="mt-1 text-sm text-muted">Human decisions before risky execution.</p>
            </div>
          </div>
          <div className="p-5">
            <ApprovalsPanel
              approvals={data.approvals.data}
              isDeciding={data.approve.isPending || data.reject.isPending}
              isLoading={data.approvals.isLoading}
              onApprove={(approvalId) => data.approve.mutate(approvalId)}
              onReject={(approvalId) => data.reject.mutate(approvalId)}
            />
          </div>
        </section>

        <section className="panel">
          <div className="panel-header">
            <div>
              <h2 className="text-base font-semibold">Reconciliation</h2>
              <p className="mt-1 text-sm text-muted">Read-only consistency checks.</p>
            </div>
          </div>
          <div className="p-5">
            <ReconciliationPanel
              isRunning={data.reconciliation.isPending}
              onRun={() => data.reconciliation.mutate()}
              summary={data.reconciliation.data}
            />
          </div>
        </section>
      </div>

      <section className="panel" id="receipts">
        <div className="panel-header">
          <div>
            <h2 className="text-base font-semibold">Receipt Lookup</h2>
            <p className="mt-1 text-sm text-muted">
              Find a completed transaction receipt and export it as JSON.
            </p>
          </div>
        </div>
        <div className="p-5">
          <ReceiptLookupPanel lookup={data.receiptLookup} />
        </div>
      </section>

      <div className="grid gap-5 xl:grid-cols-2" id="audit">
      <section className="panel">
        <div className="panel-header">
          <div>
            <h2 className="text-base font-semibold">Audit Event Timeline</h2>
            <p className="mt-1 text-sm text-muted">Security and workflow events.</p>
          </div>
        </div>
        <div className="p-5">
          <AuditEvents events={data.auditEvents.data} isLoading={data.auditEvents.isLoading} />
        </div>
      </section>

      <section className="panel">
        <div className="panel-header">
          <div>
            <h2 className="text-base font-semibold">Callback Timeline</h2>
            <p className="mt-1 text-sm text-muted">
              Callback-related events sourced from the audit trail.
            </p>
          </div>
        </div>
        <div className="p-5">
          <CallbackTimeline
            events={data.auditEvents.data}
            isLoading={data.auditEvents.isLoading}
          />
        </div>
      </section>
      </div>
    </DashboardLayout>
  );
}
