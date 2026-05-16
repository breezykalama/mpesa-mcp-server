import axios from "axios";
import type {
  AnalyticsSummary,
  ApprovalRequest,
  AuditEventSummary,
  ReconciliationSummary,
  SystemStatus,
  TransactionSummary,
} from "../types/operator";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";
const TOKEN_STORAGE_KEY = "mpesa_mcp_operator_token";

export function getStoredToken(): string {
  return localStorage.getItem(TOKEN_STORAGE_KEY) ?? "";
}

export function storeToken(token: string): void {
  localStorage.setItem(TOKEN_STORAGE_KEY, token);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_STORAGE_KEY);
}

const api = axios.create({
  baseURL: API_BASE_URL,
  timeout: 10_000,
});

api.interceptors.request.use((config) => {
  const token = getStoredToken();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

export async function fetchTodayAnalytics(): Promise<AnalyticsSummary> {
  const response = await api.get<{ summary: AnalyticsSummary }>("/operator/analytics/today");
  return response.data.summary;
}

export async function fetchTransactions(): Promise<TransactionSummary[]> {
  const response = await api.get<{ transactions: TransactionSummary[] }>(
    "/operator/transactions",
  );
  return response.data.transactions;
}

export async function fetchAuditEvents(): Promise<AuditEventSummary[]> {
  const response = await api.get<{ audit_events: AuditEventSummary[] }>(
    "/operator/audit-events",
  );
  return response.data.audit_events;
}

export async function fetchPendingApprovals(): Promise<ApprovalRequest[]> {
  const response = await api.get<{ approvals: ApprovalRequest[] }>("/approvals/pending");
  return response.data.approvals;
}

export async function approvePaymentRequest(approvalId: string): Promise<void> {
  await api.post(`/approvals/${approvalId}/approve`);
}

export async function rejectPaymentRequest(approvalId: string): Promise<void> {
  await api.post(`/approvals/${approvalId}/reject`);
}

export async function runReconciliation(): Promise<ReconciliationSummary> {
  const response = await api.post<{ summary: ReconciliationSummary }>(
    "/operator/reconciliation/run",
  );
  return response.data.summary;
}

export async function fetchHealth(): Promise<SystemStatus> {
  const response = await api.get<SystemStatus>("/health");
  return response.data;
}

export function describeApiError(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data as { detail?: string; reason?: string } | undefined;
    return detail?.detail ?? detail?.reason ?? error.message;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return "Something went wrong.";
}
