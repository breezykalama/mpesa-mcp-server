export interface AnalyticsSummary {
  date: string;
  total_transactions: number;
  completed_transactions: number;
  failed_transactions: number;
  pending_transactions: number;
  total_revenue: number;
}

export interface TransactionSummary {
  transaction_id: string;
  provider: string;
  rail: string;
  status: string;
  amount: number;
  phone_number: string;
  created_at: string;
  provider_transaction_id: string | null;
  provider_reference: string | null;
}

export interface AuditEventSummary {
  event_id: string;
  event_type: string;
  created_at: string;
  actor: string | null;
  correlation_id: string | null;
}

export interface ApprovalRequest {
  approval_id: string;
  action: string;
  payload: Record<string, unknown>;
  reason: string;
  status: "pending" | "approved" | "rejected" | "expired";
  created_at: string;
  expires_at: string;
  reviewed_at: string | null;
  expired_at: string | null;
  required_reviewers: number;
  reviewer_ids: string[];
  review_count: number;
}

export interface ReconciliationFinding {
  finding_type: string;
  transaction_id: string;
  checkout_request_id: string;
  local_status: string;
  provider_status: string | null;
  provider: string;
  rail: string;
  reason: string;
}

export interface ReconciliationSummary {
  status: string;
  checked_transactions: number;
  finding_count: number;
  pending_local_but_provider_completed: number;
  pending_local_but_provider_failed: number;
  completed_local_but_provider_failed: number;
  failed_local_but_provider_completed: number;
  stale_pending_transaction: number;
  provider_status_unknown: number;
  findings: ReconciliationFinding[];
}

export interface SystemStatus {
  status: string;
  ready?: boolean;
  payment_provider?: string;
  storage_mode: string;
  rate_limit_mode?: string;
  auth_mode?: string;
}

export interface ReceiptPayload {
  receipt_id: string;
  transaction_id: string;
  checkout_request_id: string;
  mpesa_receipt_number: string;
  amount: number;
  phone_number: string;
  status: string;
  issued_at: string;
}

export interface ReceiptLookupResponse {
  status: string;
  allowed: boolean;
  reason: string;
  receipt: ReceiptPayload | null;
}
