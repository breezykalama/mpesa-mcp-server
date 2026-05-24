import type { TransactionSummary } from "../types/operator";

export type TransactionSortKey = "created_at" | "amount" | "status" | "provider" | "rail";
export type SortDirection = "asc" | "desc";

export interface TransactionFilters {
  provider: string;
  rail: string;
  status: string;
  search: string;
  sortKey: TransactionSortKey;
  sortDirection: SortDirection;
}

export function filterAndSortTransactions(
  transactions: TransactionSummary[],
  filters: TransactionFilters,
): TransactionSummary[] {
  const normalizedSearch = filters.search.trim().toLowerCase();

  return transactions
    .filter((transaction) => {
      const matchesProvider =
        filters.provider === "all" || transaction.provider === filters.provider;
      const matchesRail = filters.rail === "all" || transaction.rail === filters.rail;
      const matchesStatus =
        filters.status === "all" || transaction.status === filters.status;
      const searchable = [
        transaction.phone_number,
        transaction.provider_transaction_id,
        transaction.provider_reference,
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      const matchesSearch =
        normalizedSearch === "" || searchable.includes(normalizedSearch);

      return matchesProvider && matchesRail && matchesStatus && matchesSearch;
    })
    .sort((left, right) => compareTransactions(left, right, filters));
}

function compareTransactions(
  left: TransactionSummary,
  right: TransactionSummary,
  filters: TransactionFilters,
): number {
  const direction = filters.sortDirection === "asc" ? 1 : -1;
  if (filters.sortKey === "amount") {
    return (left.amount - right.amount) * direction;
  }
  if (filters.sortKey === "created_at") {
    return (
      (new Date(left.created_at).getTime() - new Date(right.created_at).getTime()) *
      direction
    );
  }

  return String(left[filters.sortKey]).localeCompare(String(right[filters.sortKey])) * direction;
}
