export function SkeletonRows({ rows = 4 }: { rows?: number }) {
  return (
    <div className="space-y-3">
      {Array.from({ length: rows }).map((_, index) => (
        <div
          className="h-10 animate-pulse rounded-md bg-slate-100"
          key={`skeleton-${index}`}
        />
      ))}
    </div>
  );
}

export function SkeletonCards() {
  return (
    <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
      {Array.from({ length: 4 }).map((_, index) => (
        <div className="panel h-32 animate-pulse bg-slate-100" key={`card-${index}`} />
      ))}
    </section>
  );
}
