import {
  Activity,
  BarChart3,
  ClipboardCheck,
  History,
  LogOut,
  ReceiptText,
  RefreshCw,
  ShieldCheck,
} from "lucide-react";
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
  const navItems = [
    { href: "#overview", label: "Overview", icon: BarChart3 },
    { href: "#transactions", label: "Transactions", icon: Activity },
    { href: "#approvals", label: "Approvals", icon: ClipboardCheck },
    { href: "#receipts", label: "Receipts", icon: ReceiptText },
    { href: "#audit", label: "Audit", icon: History },
  ];

  return (
    <div className="min-h-screen bg-canvas">
      <header className="sticky top-0 z-30 border-b border-line bg-white/95 backdrop-blur">
        <div className="mx-auto flex max-w-7xl flex-col gap-4 px-4 py-4 sm:px-6 lg:px-8 xl:flex-row xl:items-center xl:justify-between">
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
        <nav className="mx-auto flex max-w-7xl gap-1 overflow-x-auto px-4 pb-3 sm:px-6 lg:px-8">
          {navItems.map((item) => (
            <a
              className="inline-flex items-center gap-2 rounded-md px-3 py-2 text-sm font-medium text-muted transition hover:bg-slate-100 hover:text-ink"
              href={item.href}
              key={item.href}
            >
              <item.icon className="h-4 w-4" />
              {item.label}
            </a>
          ))}
        </nav>
      </header>
      <div className="mx-auto grid max-w-7xl gap-5 px-4 py-6 sm:px-6 lg:px-8">
        {children}
      </div>
    </div>
  );
}
