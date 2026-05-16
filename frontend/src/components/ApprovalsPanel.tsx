import { CheckCircle2, XCircle } from "lucide-react";
import type { ApprovalRequest } from "../types/operator";
import { EmptyState } from "./EmptyState";
import { SkeletonRows } from "./Skeleton";

interface ApprovalsPanelProps {
  approvals?: ApprovalRequest[];
  isLoading: boolean;
  onApprove: (approvalId: string) => void;
  onReject: (approvalId: string) => void;
  isDeciding: boolean;
}

export function ApprovalsPanel({
  approvals,
  isLoading,
  isDeciding,
  onApprove,
  onReject,
}: ApprovalsPanelProps) {
  if (isLoading) {
    return <SkeletonRows />;
  }

  if (!approvals?.length) {
    return <EmptyState message="Risky payments will appear here." title="No pending approvals" />;
  }

  return (
    <div className="space-y-3">
      {approvals.map((approval) => (
        <article className="rounded-lg border border-line bg-slate-50 p-4" key={approval.approval_id}>
          <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
            <div>
              <p className="text-sm font-semibold text-ink">{approval.action}</p>
              <p className="mt-1 text-sm text-muted">{approval.reason}</p>
              <p className="mt-2 text-xs text-muted">
                {approval.approval_id} · {new Date(approval.created_at).toLocaleString()}
              </p>
            </div>
            <div className="flex gap-2">
              <button
                className="button button-primary"
                disabled={isDeciding}
                onClick={() => onApprove(approval.approval_id)}
                type="button"
              >
                <CheckCircle2 className="h-4 w-4" />
                Approve
              </button>
              <button
                className="button button-danger"
                disabled={isDeciding}
                onClick={() => onReject(approval.approval_id)}
                type="button"
              >
                <XCircle className="h-4 w-4" />
                Reject
              </button>
            </div>
          </div>
        </article>
      ))}
    </div>
  );
}
