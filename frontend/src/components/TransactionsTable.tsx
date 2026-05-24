import type { TransactionSummary } from "../types/operator";
import { formatDateTime, relativeTime } from "../utils/date";
import {
  filterAndSortTransactions,
  SortDirection,
  TransactionSortKey,
} from "../utils/transactions";
import { EmptyState } from "./EmptyState";
import { SkeletonRows } from "./Skeleton";
import { StatusBadge } from "./StatusBadge";
import { useMemo, useState } from "react";

interface TransactionsTableProps {
  transactions?: TransactionSummary[];
  isLoading: boolean;
}

export function TransactionsTable({ transactions, isLoading }: TransactionsTableProps) {
  const [provider, setProvider] = useState("all");
  const [rail, setRail] = useState("all");
  const [status, setStatus] = useState("all");
  const [search, setSearch] = useState("");
  const [sortKey, setSortKey] = useState<TransactionSortKey>("created_at");
  const [sortDirection, setSortDirection] = useState<SortDirection>("desc");
  const providers = useMemo(() => uniqueValues(transactions ?? [], "provider"), [transactions]);
  const rails = useMemo(() => uniqueValues(transactions ?? [], "rail"), [transactions]);
  const statuses = useMemo(() => uniqueValues(transactions ?? [], "status"), [transactions]);
  const filteredTransactions = useMemo(
    () =>
      filterAndSortTransactions(transactions ?? [], {
        provider,
        rail,
        status,
        search,
        sortKey,
        sortDirection,
      }),
    [provider, rail, search, sortDirection, sortKey, status, transactions],
  );

  if (isLoading) {
    return <div className="p-5"><SkeletonRows rows={6} /></div>;
  }

  if (!transactions?.length) {
    return (
      <EmptyState
        message="Initiate a mock payment or run the smoke script to populate this table."
        title="No transactions yet"
      />
    );
  }

  return (
    <div>
      <div className="grid gap-3 border-b border-line p-4 lg:grid-cols-[1.2fr_repeat(4,minmax(0,1fr))]">
        <input
          className="min-h-10 rounded-md border border-line px-3 text-sm outline-none ring-money/20 focus:ring-4"
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Search phone or provider transaction ID"
          value={search}
        />
        <select className="rounded-md border border-line px-3 text-sm" onChange={(event) => setProvider(event.target.value)} value={provider}>
          <option value="all">All providers</option>
          {providers.map((value) => <option key={value} value={value}>{value}</option>)}
        </select>
        <select className="rounded-md border border-line px-3 text-sm" onChange={(event) => setRail(event.target.value)} value={rail}>
          <option value="all">All rails</option>
          {rails.map((value) => <option key={value} value={value}>{value}</option>)}
        </select>
        <select className="rounded-md border border-line px-3 text-sm" onChange={(event) => setStatus(event.target.value)} value={status}>
          <option value="all">All statuses</option>
          {statuses.map((value) => <option key={value} value={value}>{value}</option>)}
        </select>
        <select
          className="rounded-md border border-line px-3 text-sm"
          onChange={(event) => {
            const [nextSortKey, nextDirection] = event.target.value.split(":") as [
              TransactionSortKey,
              SortDirection,
            ];
            setSortKey(nextSortKey);
            setSortDirection(nextDirection);
          }}
          value={`${sortKey}:${sortDirection}`}
        >
          <option value="created_at:desc">Newest first</option>
          <option value="created_at:asc">Oldest first</option>
          <option value="amount:desc">Amount high to low</option>
          <option value="amount:asc">Amount low to high</option>
          <option value="status:asc">Status A-Z</option>
        </select>
      </div>
      {filteredTransactions.length === 0 ? (
        <div className="p-5">
          <EmptyState message="Adjust filters or search terms." title="No matching transactions" />
        </div>
      ) : (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[720px] text-left text-sm">
        <thead className="border-b border-line bg-slate-50 text-muted">
          <tr>
            <th className="px-4 py-3 font-semibold">Provider</th>
            <th className="px-4 py-3 font-semibold">Rail</th>
            <th className="px-4 py-3 font-semibold">Status</th>
            <th className="px-4 py-3 font-semibold">Amount</th>
            <th className="px-4 py-3 font-semibold">Phone</th>
            <th className="px-4 py-3 font-semibold">Created</th>
          </tr>
        </thead>
        <tbody>
          {filteredTransactions.map((transaction) => (
            <tr className="border-b border-line last:border-0" key={transaction.transaction_id}>
              <td className="px-4 py-3 font-medium">{transaction.provider}</td>
              <td className="px-4 py-3 text-muted">{transaction.rail}</td>
              <td className="px-4 py-3">
                <StatusBadge status={transaction.status} />
              </td>
              <td className="px-4 py-3">KES {transaction.amount.toLocaleString()}</td>
              <td className="px-4 py-3 text-muted">
                <div>{transaction.phone_number}</div>
                <div className="mt-1 text-xs">{transaction.provider_transaction_id ?? "no provider id"}</div>
              </td>
              <td className="px-4 py-3 text-muted">
                <div>{relativeTime(transaction.created_at)}</div>
                <div className="mt-1 text-xs">{formatDateTime(transaction.created_at)}</div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
      )}
    </div>
  );
}

function uniqueValues(
  transactions: TransactionSummary[],
  key: keyof Pick<TransactionSummary, "provider" | "rail" | "status">,
) {
  return Array.from(new Set(transactions.map((transaction) => transaction[key]))).sort();
}
