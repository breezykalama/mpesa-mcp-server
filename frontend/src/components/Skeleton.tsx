export function SkeletonRows() {
  return (
    <div className="space-y-3">
      {Array.from({ length: 4 }).map((_, index) => (
        <div
          className="h-10 animate-pulse rounded-md bg-slate-100"
          key={`skeleton-${index}`}
        />
      ))}
    </div>
  );
}
