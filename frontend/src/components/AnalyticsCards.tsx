import { Banknote, Clock3, ReceiptText, TrendingUp } from "lucide-react";
import type { AnalyticsSummary } from "../types/operator";
import { MetricCard } from "./MetricCard";
import { SkeletonRows } from "./Skeleton";

interface AnalyticsCardsProps {
  summary?: AnalyticsSummary;
  isLoading: boolean;
}

export function AnalyticsCards({ summary, isLoading }: AnalyticsCardsProps) {
  if (isLoading) {
    return <SkeletonRows />;
  }

  const safeSummary =
    summary ??
    ({
      total_transactions: 0,
      completed_transactions: 0,
      pending_transactions: 0,
      total_revenue: 0,
    } as AnalyticsSummary);

  return (
    <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
      <MetricCard
        detail="All transactions created today"
        icon={ReceiptText}
        label="Total transactions"
        value={safeSummary.total_transactions}
      />
      <MetricCard
        detail="Completed payments only"
        icon={TrendingUp}
        label="Completed"
        value={safeSummary.completed_transactions}
      />
      <MetricCard
        detail="Awaiting provider or callback state"
        icon={Clock3}
        label="Pending"
        value={safeSummary.pending_transactions}
      />
      <MetricCard
        detail="Revenue counts completed transactions"
        icon={Banknote}
        label="Revenue"
        value={`KES ${safeSummary.total_revenue.toLocaleString()}`}
      />
    </section>
  );
}
