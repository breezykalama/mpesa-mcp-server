import { AlertTriangle, Banknote, ClipboardCheck, Network, SearchX } from "lucide-react";
import type { AnalyticsSummary, ApprovalRequest, ReconciliationSummary, SystemStatus } from "../types/operator";
import { MetricCard } from "./MetricCard";
import { SkeletonCards } from "./Skeleton";

interface AnalyticsCardsProps {
  summary?: AnalyticsSummary;
  approvals?: ApprovalRequest[];
  reconciliation?: ReconciliationSummary;
  system?: SystemStatus;
  isLoading: boolean;
}

export function AnalyticsCards({
  summary,
  approvals,
  reconciliation,
  system,
  isLoading,
}: AnalyticsCardsProps) {
  if (isLoading) {
    return <SkeletonCards />;
  }

  const safeSummary =
    summary ??
    ({
      total_transactions: 0,
      completed_transactions: 0,
      pending_transactions: 0,
      failed_transactions: 0,
      total_revenue: 0,
    } as AnalyticsSummary);

  return (
    <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-5" id="overview">
      <MetricCard
        detail="Completed transactions only"
        icon={Banknote}
        label="Today revenue"
        value={`KES ${safeSummary.total_revenue.toLocaleString()}`}
      />
      <MetricCard
        detail="Transactions marked failed today"
        icon={AlertTriangle}
        label="Failed payments"
        value={safeSummary.failed_transactions}
      />
      <MetricCard
        detail="Awaiting human decision"
        icon={ClipboardCheck}
        label="Pending approvals"
        value={approvals?.length ?? 0}
      />
      <MetricCard
        detail="Latest reconciliation result"
        icon={SearchX}
        label="Recon findings"
        value={reconciliation?.finding_count ?? 0}
      />
      <MetricCard
        detail="Configured active rail"
        icon={Network}
        label="Active provider"
        value={system?.payment_provider ?? "unknown"}
      />
    </section>
  );
}
