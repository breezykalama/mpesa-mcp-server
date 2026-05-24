import { Download, Search } from "lucide-react";
import { FormEvent, useState } from "react";
import type { UseMutationResult } from "@tanstack/react-query";
import type { ReceiptLookupResponse } from "../../types/operator";
import { EmptyState } from "../../components/EmptyState";
import { ErrorBanner } from "../../components/ErrorBanner";
import { describeApiError } from "../../api/client";

interface ReceiptLookupPanelProps {
  lookup: UseMutationResult<ReceiptLookupResponse, Error, string>;
}

export function ReceiptLookupPanel({ lookup }: ReceiptLookupPanelProps) {
  const [reference, setReference] = useState("");

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmedReference = reference.trim();
    if (trimmedReference) {
      lookup.mutate(trimmedReference);
    }
  }

  function downloadReceipt() {
    if (!lookup.data?.receipt) {
      return;
    }
    const blob = new Blob([JSON.stringify(lookup.data.receipt, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${lookup.data.receipt.receipt_id}.json`;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="space-y-4">
      <form className="flex flex-col gap-3 sm:flex-row" onSubmit={submit}>
        <input
          className="min-h-10 flex-1 rounded-md border border-line px-3 text-sm outline-none ring-money/20 focus:ring-4"
          onChange={(event) => setReference(event.target.value)}
          placeholder="Checkout request ID or provider transaction ID"
          value={reference}
        />
        <button className="button button-primary" disabled={lookup.isPending} type="submit">
          <Search className="h-4 w-4" />
          {lookup.isPending ? "Looking up..." : "Lookup receipt"}
        </button>
      </form>

      {lookup.error ? <ErrorBanner message={describeApiError(lookup.error)} /> : null}

      {!lookup.data ? (
        <EmptyState
          message="Completed transactions with receipt numbers can be exported as JSON."
          title="Search for a receipt"
        />
      ) : lookup.data.receipt ? (
        <div className="rounded-lg border border-line bg-slate-50 p-4">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p className="text-sm font-semibold">{lookup.data.receipt.receipt_id}</p>
              <p className="mt-1 text-sm text-muted">
                {lookup.data.receipt.mpesa_receipt_number} · KES{" "}
                {lookup.data.receipt.amount.toLocaleString()}
              </p>
            </div>
            <button className="button" onClick={downloadReceipt} type="button">
              <Download className="h-4 w-4" />
              Export JSON
            </button>
          </div>
          <pre className="mt-4 overflow-x-auto rounded-md bg-white p-3 text-xs text-muted">
            {JSON.stringify(lookup.data.receipt, null, 2)}
          </pre>
        </div>
      ) : (
        <EmptyState message={lookup.data.reason} title={lookup.data.status} />
      )}
    </div>
  );
}
