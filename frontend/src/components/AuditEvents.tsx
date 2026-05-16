import type { AuditEventSummary } from "../types/operator";
import { EmptyState } from "./EmptyState";
import { SkeletonRows } from "./Skeleton";

interface AuditEventsProps {
  events?: AuditEventSummary[];
  isLoading: boolean;
}

export function AuditEvents({ events, isLoading }: AuditEventsProps) {
  if (isLoading) {
    return <SkeletonRows />;
  }

  if (!events?.length) {
    return <EmptyState message="Security and payment events will appear here." title="No audit events" />;
  }

  return (
    <div className="space-y-3">
      {events.map((event) => (
        <div className="rounded-lg border border-line p-3" key={event.event_id}>
          <div className="flex items-start justify-between gap-3">
            <p className="text-sm font-semibold text-ink">{event.event_type}</p>
            <p className="text-xs text-muted">{new Date(event.created_at).toLocaleString()}</p>
          </div>
          <p className="mt-2 text-xs text-muted">
            Actor: {event.actor ?? "system"} · Correlation: {event.correlation_id ?? "none"}
          </p>
        </div>
      ))}
    </div>
  );
}
