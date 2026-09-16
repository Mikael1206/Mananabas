"use client";

import { useEffect } from "react";

// Without this, an uncaught render error in a client component (e.g. inside
// the Google sign-in flow) leaves the page blank with no way to recover —
// Next.js's App Router needs a segment-level error boundary to show
// anything at all. This also logs the error so it shows up in the browser
// console instead of silently failing.
export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error("Mananabas crashed:", error);
  }, [error]);

  return (
    <main className="min-h-screen flex flex-col items-center justify-center px-6 py-16 gap-4">
      <h1 className="text-2xl font-bold">Something went wrong</h1>
      <p className="text-neutral-400 text-center max-w-md">
        {error.message || "An unexpected error occurred while loading Mananabas."}
      </p>
      <div className="flex gap-3">
        <button
          onClick={reset}
          className="rounded-md bg-white text-black px-5 py-2 font-medium"
        >
          Try again
        </button>
        <button
          onClick={() => {
            window.localStorage.removeItem("mananabas_auth");
            window.location.href = "/";
          }}
          className="rounded-md border border-neutral-700 px-5 py-2 font-medium text-neutral-300"
        >
          Sign out &amp; reload
        </button>
      </div>
    </main>
  );
}
