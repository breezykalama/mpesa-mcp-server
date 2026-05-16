import { PlayCircle } from "lucide-react";
import type { ReconciliationSummary } from "../types/operator";
import { EmptyState } from "./EmptyState";

interface ReconciliationPanelProps {
  summary?: ReconciliationSummary;
  isRunning: boolean;
  onRun: () => void;
}

export function ReconciliationPanel({
  summary,
  isRunning,
  onRun,
}: ReconciliationPanelProps) {
  return (
    <div className="space-y-4">
      <button className="button button-primary" disabled={isRunning} onClick={onRun} type="button">
        <PlayCircle className="h-4 w-4" />
        {isRunning ? "Running..." : "Run reconciliation"}
      </button>
      {!summary ? (
        <EmptyState
          message="Run reconciliation to compare local records with provider state."
          title="No reconciliation run yet"
        />
      ) : (
        <div className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-3">
            <div className="rounded-lg border border-line p-3">
              <p className="text-xs text-muted">Checked</p>
              <p className="mt-1 text-xl font-semibold">{summary.checked_transactions}</p>
            </div>
            <div className="rounded-lg border border-line p-3">
              <p className="text-xs text-muted">Findings</p>
              <p className="mt-1 text-xl font-semibold">{summary.finding_count}</p>
            </div>
            <div className="rounded-lg border border-line p-3">
              <p className="text-xs text-muted">Status</p>
              <p className="mt-1 text-xl font-semibold">{summary.status}</p>
            </div>
          </div>
          {summary.findings.slice(0, 4).map((finding) => (
            <div className="rounded-lg border border-amber-200 bg-amber-50 p-3" key={finding.transaction_id}>
              <p className="text-sm font-semibold text-ember">{finding.finding_type}</p>
              <p className="mt-1 text-sm text-muted">{finding.reason}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
