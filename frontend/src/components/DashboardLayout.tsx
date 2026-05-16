import { LogOut, RefreshCw, ShieldCheck } from "lucide-react";
import { PropsWithChildren } from "react";

interface DashboardLayoutProps extends PropsWithChildren {
  onLogout: () => void;
  onRefresh: () => void;
  isRefreshing: boolean;
}

export function DashboardLayout({
  children,
  onLogout,
  onRefresh,
  isRefreshing,
}: DashboardLayoutProps) {
  return (
    <div className="min-h-screen bg-canvas">
      <header className="border-b border-line bg-white">
        <div className="mx-auto flex max-w-7xl flex-col gap-4 px-4 py-5 sm:px-6 lg:px-8 xl:flex-row xl:items-center xl:justify-between">
          <div>
            <div className="flex items-center gap-2 text-sm font-medium text-money">
              <ShieldCheck className="h-4 w-4" />
              Operator Console
            </div>
            <h1 className="mt-2 text-2xl font-semibold tracking-normal text-ink">
              M-Pesa MCP Server
            </h1>
            <p className="mt-1 text-sm text-muted">
              Payments, approvals, audit events, and reconciliation in one view.
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <button className="button" disabled={isRefreshing} onClick={onRefresh} type="button">
              <RefreshCw className={`h-4 w-4 ${isRefreshing ? "animate-spin" : ""}`} />
              Refresh
            </button>
            <button className="button" onClick={onLogout} type="button">
              <LogOut className="h-4 w-4" />
              Sign out
            </button>
          </div>
        </div>
      </header>
      <div className="mx-auto grid max-w-7xl gap-5 px-4 py-6 sm:px-6 lg:px-8">
        {children}
      </div>
    </div>
  );
}
