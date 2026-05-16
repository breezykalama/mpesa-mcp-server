import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  approvePaymentRequest,
  fetchAuditEvents,
  fetchHealth,
  fetchPendingApprovals,
  fetchTodayAnalytics,
  fetchTransactions,
  rejectPaymentRequest,
  runReconciliation,
} from "../api/client";

export function useOperatorData(isAuthenticated: boolean) {
  const queryClient = useQueryClient();
  const enabled = isAuthenticated;

  const analytics = useQuery({
    queryKey: ["analytics", "today"],
    queryFn: fetchTodayAnalytics,
    enabled,
  });
  const transactions = useQuery({
    queryKey: ["transactions"],
    queryFn: fetchTransactions,
    enabled,
  });
  const approvals = useQuery({
    queryKey: ["approvals", "pending"],
    queryFn: fetchPendingApprovals,
    enabled,
  });
  const auditEvents = useQuery({
    queryKey: ["audit-events"],
    queryFn: fetchAuditEvents,
    enabled,
  });
  const health = useQuery({
    queryKey: ["health"],
    queryFn: fetchHealth,
  });

  const invalidateDashboard = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["analytics"] }),
      queryClient.invalidateQueries({ queryKey: ["transactions"] }),
      queryClient.invalidateQueries({ queryKey: ["approvals"] }),
      queryClient.invalidateQueries({ queryKey: ["audit-events"] }),
    ]);
  };

  const approve = useMutation({
    mutationFn: approvePaymentRequest,
    onSuccess: invalidateDashboard,
  });
  const reject = useMutation({
    mutationFn: rejectPaymentRequest,
    onSuccess: invalidateDashboard,
  });
  const reconciliation = useMutation({
    mutationFn: runReconciliation,
  });

  return {
    analytics,
    transactions,
    approvals,
    auditEvents,
    health,
    approve,
    reject,
    reconciliation,
  };
}
