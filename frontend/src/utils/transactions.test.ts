import { describe, expect, it } from "vitest";
import { filterAndSortTransactions } from "./transactions";
import type { TransactionSummary } from "../types/operator";

const transactions: TransactionSummary[] = [
  {
    transaction_id: "1",
    provider: "daraja",
    rail: "mpesa",
    status: "pending",
    amount: 1000,
    phone_number: "254700000001",
    created_at: "2026-05-24T08:00:00Z",
    provider_transaction_id: "ws_CO_1",
    provider_reference: "mock_1",
  },
  {
    transaction_id: "2",
    provider: "airtel",
    rail: "airtel_money",
    status: "completed",
    amount: 2000,
    phone_number: "254700000002",
    created_at: "2026-05-24T09:00:00Z",
    provider_transaction_id: "airtel_txn_2",
    provider_reference: "airtel_ref_2",
  },
];

describe("filterAndSortTransactions", () => {
  it("filters by provider, status, and provider transaction search", () => {
    const result = filterAndSortTransactions(transactions, {
      provider: "airtel",
      rail: "all",
      status: "completed",
      search: "airtel_txn",
      sortKey: "created_at",
      sortDirection: "desc",
    });

    expect(result).toHaveLength(1);
    expect(result[0].transaction_id).toBe("2");
  });

  it("sorts amounts descending", () => {
    const result = filterAndSortTransactions(transactions, {
      provider: "all",
      rail: "all",
      status: "all",
      search: "",
      sortKey: "amount",
      sortDirection: "desc",
    });

    expect(result.map((transaction) => transaction.amount)).toEqual([2000, 1000]);
  });
});
