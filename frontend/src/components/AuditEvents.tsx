import type { AuditEventSummary } from "../types/operator";
import { formatDateTime, relativeTime } from "../utils/date";
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
    <div className="relative space-y-0">
      {events.map((event) => (
        <div className="relative border-l border-line py-4 pl-5" key={event.event_id}>
          <span className="absolute -left-1.5 top-5 h-3 w-3 rounded-full border-2 border-white bg-money" />
          <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
            <div>
              <span className="tag border-emerald-200 bg-emerald-50 text-money">
                {event.event_type}
              </span>
              <p className="mt-2 text-sm text-muted">
                Actor: {event.actor ?? "system"} · Correlation: {event.correlation_id ?? "none"}
              </p>
            </div>
            <p className="text-xs text-muted" title={formatDateTime(event.created_at)}>
              {relativeTime(event.created_at)}
            </p>
          </div>
        </div>
      ))}
    </div>
  );
}

export function CallbackTimeline({ events, isLoading }: AuditEventsProps) {
  const callbackEvents = (events ?? []).filter((event) =>
    event.event_type.toLowerCase().includes("callback"),
  );

  if (isLoading) {
    return <SkeletonRows rows={3} />;
  }

  if (!callbackEvents.length) {
    return (
      <EmptyState
        message="Callback audit events will appear after STK callbacks are processed."
        title="No callback events yet"
      />
    );
  }

  return <AuditEvents events={callbackEvents} isLoading={false} />;
}
