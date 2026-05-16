import { Activity, Server } from "lucide-react";
import type { SystemStatus } from "../types/operator";
import { StatusBadge } from "./StatusBadge";

interface SystemStatusPanelProps {
  status?: SystemStatus;
  isLoading: boolean;
}

export function SystemStatusPanel({ status, isLoading }: SystemStatusPanelProps) {
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <div className="rounded-lg border border-line p-4">
        <div className="flex items-center gap-2 text-sm text-muted">
          <Activity className="h-4 w-4" />
          API status
        </div>
        <div className="mt-3">
          {isLoading ? <span className="text-sm text-muted">Checking...</span> : <StatusBadge status={status?.status ?? "unknown"} />}
        </div>
      </div>
      <div className="rounded-lg border border-line p-4">
        <div className="flex items-center gap-2 text-sm text-muted">
          <Server className="h-4 w-4" />
          Storage mode
        </div>
        <p className="mt-3 text-lg font-semibold">{status?.storage_mode ?? "unknown"}</p>
      </div>
    </div>
  );
}
