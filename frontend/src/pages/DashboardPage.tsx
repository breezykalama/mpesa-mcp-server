import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { clearToken, describeApiError, getStoredToken } from "../api/client";
import { AnalyticsCards } from "../components/AnalyticsCards";
import { ApprovalsPanel } from "../components/ApprovalsPanel";
import { AuditEvents } from "../components/AuditEvents";
import { DashboardLayout } from "../components/DashboardLayout";
import { ErrorBanner } from "../components/ErrorBanner";
import { LoginScreen } from "../components/LoginScreen";
import { ReconciliationPanel } from "../components/ReconciliationPanel";
import { SystemStatusPanel } from "../components/SystemStatusPanel";
import { TransactionsTable } from "../components/TransactionsTable";
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

      <AnalyticsCards isLoading={data.analytics.isLoading} summary={data.analytics.data} />

      <div className="grid gap-5 xl:grid-cols-[1.35fr_0.65fr]">
        <section className="panel overflow-hidden">
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
        <section className="panel">
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

      <section className="panel">
        <div className="panel-header">
          <div>
            <h2 className="text-base font-semibold">Recent Audit Events</h2>
            <p className="mt-1 text-sm text-muted">Security and workflow events.</p>
          </div>
        </div>
        <div className="p-5">
          <AuditEvents events={data.auditEvents.data} isLoading={data.auditEvents.isLoading} />
        </div>
      </section>
    </DashboardLayout>
  );
}
