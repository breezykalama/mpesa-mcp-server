import type { TransactionSummary } from "../types/operator";
import { EmptyState } from "./EmptyState";
import { SkeletonRows } from "./Skeleton";
import { StatusBadge } from "./StatusBadge";

interface TransactionsTableProps {
  transactions?: TransactionSummary[];
  isLoading: boolean;
}

export function TransactionsTable({ transactions, isLoading }: TransactionsTableProps) {
  if (isLoading) {
    return <SkeletonRows />;
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
          {transactions.map((transaction) => (
            <tr className="border-b border-line last:border-0" key={transaction.transaction_id}>
              <td className="px-4 py-3 font-medium">{transaction.provider}</td>
              <td className="px-4 py-3 text-muted">{transaction.rail}</td>
              <td className="px-4 py-3">
                <StatusBadge status={transaction.status} />
              </td>
              <td className="px-4 py-3">KES {transaction.amount.toLocaleString()}</td>
              <td className="px-4 py-3 text-muted">{transaction.phone_number}</td>
              <td className="px-4 py-3 text-muted">
                {new Date(transaction.created_at).toLocaleString()}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
