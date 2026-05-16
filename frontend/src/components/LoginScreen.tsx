import { KeyRound, ShieldCheck } from "lucide-react";
import { FormEvent, useState } from "react";
import { storeToken } from "../api/client";

interface LoginScreenProps {
  onAuthenticated: () => void;
}

export function LoginScreen({ onAuthenticated }: LoginScreenProps) {
  const [token, setToken] = useState("");

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmedToken = token.trim();
    if (!trimmedToken) {
      return;
    }
    storeToken(trimmedToken);
    onAuthenticated();
  }

  return (
    <main className="min-h-screen bg-canvas px-4 py-10">
      <section className="mx-auto grid max-w-5xl overflow-hidden rounded-lg border border-line bg-white shadow-panel md:grid-cols-[1.1fr_0.9fr]">
        <div className="p-8 md:p-10">
          <div className="mb-10 inline-flex items-center gap-2 rounded-full border border-emerald-200 bg-emerald-50 px-3 py-1 text-sm font-medium text-money">
            <ShieldCheck className="h-4 w-4" />
            Operator access
          </div>
          <h1 className="max-w-xl text-3xl font-semibold tracking-normal text-ink md:text-4xl">
            M-Pesa MCP operator dashboard
          </h1>
          <p className="mt-4 max-w-xl text-base leading-7 text-muted">
            Monitor payment activity, review approvals, inspect audit events, and run
            reconciliation from one focused demo console.
          </p>
          <form className="mt-8 max-w-xl space-y-4" onSubmit={submit}>
            <label className="block text-sm font-medium text-muted" htmlFor="operator-token">
              Operator bearer token
            </label>
            <div className="flex flex-col gap-3 sm:flex-row">
              <div className="relative flex-1">
                <KeyRound className="pointer-events-none absolute left-3 top-3 h-4 w-4 text-muted" />
                <input
                  className="min-h-10 w-full rounded-md border border-line pl-10 pr-3 text-sm outline-none ring-money/20 focus:ring-4"
                  id="operator-token"
                  onChange={(event) => setToken(event.target.value)}
                  placeholder="Paste token"
                  type="password"
                  value={token}
                />
              </div>
              <button className="button button-primary" type="submit">
                Open dashboard
              </button>
            </div>
          </form>
        </div>
        <div className="border-t border-line bg-slate-50 p-8 md:border-l md:border-t-0 md:p-10">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-muted">
            Demo scope
          </h2>
          <ul className="mt-5 space-y-4 text-sm text-muted">
            <li>Uses existing FastAPI operator APIs.</li>
            <li>Stores the token only in browser localStorage.</li>
            <li>No hardcoded credentials or real payment calls.</li>
            <li>Designed for portfolio and reviewer demos.</li>
          </ul>
        </div>
      </section>
    </main>
  );
}
