import { Component, ErrorInfo, ReactNode } from "react";
import { ErrorBanner } from "./ErrorBanner";

interface ErrorBoundaryProps {
  children: ReactNode;
}

interface ErrorBoundaryState {
  error: Error | null;
}

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, _errorInfo: ErrorInfo) {
    console.error("Dashboard render failure", error);
  }

  render() {
    if (this.state.error) {
      return (
        <main className="min-h-screen bg-canvas p-6">
          <ErrorBanner message="The dashboard could not render. Refresh and try again." />
        </main>
      );
    }

    return this.props.children;
  }
}
