"use client";

import { GoogleLogin } from "@react-oauth/google";
import { useEffect, useRef, useState } from "react";

import { useAuth } from "./AuthContext";

const API_URL = (
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"
).replace(/\/$/, "");

type Clip = {
  id: number;
  title: string;
  hook: string | null;
  score: number | null;
  start: number;
  end: number;
  file_path: string;
};

type Job = {
  id: number;
  youtube_url: string;
  language?: string | null;
  status: string;
  progress_message: string | null;
  error: string | null;
  created_at?: string;
  clips: Clip[];
};

const ACTIVE_STATUSES = new Set([
  "queued",
  "downloading",
  "transcribing",
  "ranking",
  "rendering",
]);

const GOOGLE_CLIENT_ID = process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID || "";

export default function Home() {
  const { user, token, ready, loginWithGoogleCredential, logout } = useAuth();

  const [url, setUrl] = useState("");
  const [language, setLanguage] = useState("");
  const [job, setJob] = useState<Job | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [loggingIn, setLoggingIn] = useState(false);
  const [loginError, setLoginError] = useState<string | null>(null);
  const [history, setHistory] = useState<Job[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [copyState, setCopyState] = useState<{
    id: number;
    state: "ok" | "error";
  } | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const authHeaders = (): HeadersInit =>
    token ? { Authorization: `Bearer ${token}` } : {};

  const stopPolling = () => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  };

  const upsertHistory = (updated: Job) => {
    setHistory((prev) => {
      const idx = prev.findIndex((j) => j.id === updated.id);
      if (idx === -1) return [updated, ...prev];
      const next = [...prev];
      next[idx] = updated;
      return next;
    });
  };

  const pollJob = (jobId: number) => {
    stopPolling();
    let consecutiveFailures = 0;
    const maxConsecutiveFailures = 3;
    pollRef.current = setInterval(async () => {
      try {
        const res = await fetch(`${API_URL}/api/jobs/${jobId}`, {
          headers: authHeaders(),
        });
        if (!res.ok) {
          consecutiveFailures += 1;
          if (consecutiveFailures >= maxConsecutiveFailures) {
            stopPolling();
            setJob((prev) =>
              prev
                ? {
                    ...prev,
                    error:
                      "Unable to load job status. The backend may be unreachable.",
                  }
                : null,
            );
            return;
          }
          return;
        }
        consecutiveFailures = 0;
        const data: Job = await res.json();
        setJob(data);
        upsertHistory(data);
        if (data.status === "done" || data.status === "failed") {
          stopPolling();
        }
      } catch (err) {
        consecutiveFailures += 1;
        if (consecutiveFailures >= maxConsecutiveFailures) {
          stopPolling();
          setJob((prev) =>
            prev
              ? {
                  ...prev,
                  error:
                    "Unable to load job status. The backend may be unreachable.",
                }
              : null,
          );
          return;
        }
      }
    }, 2500);
  };

  const loadHistory = async () => {
    if (!token) return;
    setHistoryLoading(true);
    try {
      const res = await fetch(`${API_URL}/api/jobs`, {
        headers: authHeaders(),
      });
      if (!res.ok) return;
      const data: Job[] = await res.json();
      setHistory(data);
    } catch {
      // Silently ignore — history is a nice-to-have, not core flow.
    } finally {
      setHistoryLoading(false);
    }
  };

  // Load this user's clip history as soon as they're signed in.
  useEffect(() => {
    if (token) {
      loadHistory();
    } else {
      setHistory([]);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  const openHistoryJob = (entry: Job) => {
    stopPolling();
    setJob(entry);
    if (ACTIVE_STATUSES.has(entry.status)) {
      pollJob(entry.id);
    }
  };

  const submit = async () => {
    if (!url || !token) return;
    setSubmitting(true);
    setJob(null);
    try {
      const res = await fetch(`${API_URL}/api/jobs`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({ youtube_url: url, language }),
      });
      if (!res.ok) {
        let detail = res.statusText;
        try {
          const body = await res.json();
          if (typeof body === "object" && body && "detail" in body) {
            detail = String((body as { detail: unknown }).detail);
          }
        } catch {
          // response body wasn't JSON; keep statusText
        }
        throw new Error(`Failed to create job: ${res.status} ${detail}`);
      }
      const data: Job = await res.json();
      setJob(data);
      setHistory((prev) => [data, ...prev]);
      pollJob(data.id);
    } catch (error) {
      console.error(error);
      const message =
        error instanceof Error
          ? networkErrorMsg(error.message)
          : String(error);
      // Don't fabricate a Job object on submit failure. Showing the error
      // banner directly is clearer than inventing "Job #0".
      setJob({
        id: 0,
        youtube_url: url,
        language: language || null,
        status: "failed",
        progress_message: null,
        error: message,
        clips: [],
      });
      // Never poll job 0 — that id is only a UI placeholder and overwrites
      // the real create-job error with "backend may be unreachable."
    } finally {
      setSubmitting(false);
    }
  };

  useEffect(() => stopPolling, []);

  const clipPostCaption = (clip: Clip) => {
    const title = (clip.title || "").trim();
    const hook = (clip.hook || "").trim();
    if (title && hook && hook.toLowerCase() !== title.toLowerCase()) {
      return `${title}\n\n${hook}`;
    }
    return title || hook;
  };

  const writeClipboard = async (text: string) => {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return;
    }
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    document.body.removeChild(ta);
  };

  // Copy the clip's title/hook (the post caption), not the burned-in subtitles.
  const copyCaptions = async (clip: Clip) => {
    try {
      const text = clipPostCaption(clip);
      if (!text) {
        throw new Error("This clip has no title or hook to copy.");
      }
      await writeClipboard(text);
      setCopyState({ id: clip.id, state: "ok" });
    } catch (err) {
      console.error(err);
      setCopyState({ id: clip.id, state: "error" });
    } finally {
      setTimeout(
        () => setCopyState((s) => (s?.id === clip.id ? null : s)),
        2000
      );
    }
  };

  const networkErrorMsg = (message: string) => {
    // Browsers often report fetch failures as the generic "Failed to fetch".
    // Give the user something more actionable.
    if (message === "Failed to fetch") {
      return (
        "Could not reach the backend. Is the API running at " +
        API_URL + "?"
      );
    }
    if (
      message.startsWith("fetch") ||
      message.includes("NetworkError") ||
      message.includes("network") ||
      message.includes("Network")
    ) {
      return (
        "Network error reaching the backend. Is the API running at " +
        API_URL + "?"
      );
    }
    return message;
  };

  return (
    <main className="min-h-screen flex flex-col items-center px-6 py-16">
      <div className="w-full max-w-3xl flex items-center justify-between mb-8">
        <div>
          <h1 className="text-3xl font-bold mb-2">Mananabas</h1>
          <p className="text-neutral-400">
            Paste a YouTube URL, get back ranked vertical clips with captions.
          </p>
        </div>
        {ready && user && (
          <div className="flex items-center gap-3">
            {user.picture_url && (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={user.picture_url}
                alt={user.name || user.email}
                className="w-9 h-9 rounded-full"
              />
            )}
            <div className="text-sm text-right">
              <p className="font-medium">{user.name || user.email}</p>
              <button
                onClick={logout}
                className="text-neutral-400 hover:text-neutral-200 underline"
              >
                Sign out
              </button>
            </div>
          </div>
        )}
      </div>

      {ready && !user && (
        <div className="w-full max-w-xl rounded-md border border-neutral-800 p-6 flex flex-col items-center gap-3">
          <p className="text-neutral-300">Sign in with Google to start clipping.</p>
          {GOOGLE_CLIENT_ID ? (
            loggingIn ? (
              <p className="text-sm text-neutral-400 font-medium">Signing in...</p>
            ) : (
              <GoogleLogin
                onSuccess={async (credentialResponse) => {
                  setLoginError(null);
                  if (!credentialResponse.credential) {
                    setLoginError("No credential returned from Google.");
                    return;
                  }
                  setLoggingIn(true);
                  try {
                    await loginWithGoogleCredential(credentialResponse.credential);
                  } catch (err) {
                    setLoginError(
                      err instanceof Error ? err.message : String(err)
                    );
                  } finally {
                    setLoggingIn(false);
                  }
                }}
                onError={() =>
                  setLoginError("Google sign-in failed. Please try again.")
                }
                useOneTap
                theme="outline"
                shape="rectangular"
                size="large"
              />
            )
          ) : (
            <p className="text-sm text-amber-400 text-center">
              Google sign-in isn&apos;t configured yet. Set{" "}
              <code className="bg-neutral-900 px-1 rounded">
                NEXT_PUBLIC_GOOGLE_CLIENT_ID
              </code>{" "}
              in the frontend environment (and restart the dev server) before
              signing in.
            </p>
          )}
          {loginError && <p className="text-sm text-red-400">{loginError}</p>}
        </div>
      )}

      {ready && user && (
        <>
          <div className="w-full max-w-xl flex gap-2">
            <input
              className="flex-1 rounded-md bg-neutral-900 border border-neutral-700 px-4 py-2 outline-none focus:border-neutral-400"
              placeholder="https://www.youtube.com/watch?v=..."
              value={url}
              onChange={(e) => setUrl(e.target.value)}
            />
            <select
              className="rounded-md bg-neutral-900 border border-neutral-700 px-3 py-2 outline-none focus:border-neutral-400"
              value={language}
              onChange={(e) => setLanguage(e.target.value)}
              aria-label="Transcription language"
            >
              <option value="">Auto-detect</option>
              <option value="en">English</option>
              <option value="tl">Filipino / Tagalog</option>
            </select>
            <button
              onClick={submit}
              disabled={submitting || !url}
              className="rounded-md bg-white text-black px-5 py-2 font-medium disabled:opacity-40"
            >
              {submitting ? "Starting..." : "Clip it"}
            </button>
          </div>

          {job && (
            <div className="w-full max-w-xl mt-8 rounded-md border border-neutral-800 p-4">
              <p className="text-sm text-neutral-400">
                {job.id > 0 ? `Job #${job.id}` : "Job not created"}
              </p>
              <p className="mt-1 font-medium capitalize">{job.status}</p>
              {job.progress_message && (
                <p className="text-sm text-neutral-400 mt-1">{job.progress_message}</p>
              )}
              {job.error && (
                <pre className="text-xs text-red-400 mt-2 whitespace-pre-wrap">{job.error}</pre>
              )}
            </div>
          )}

          {job && job.clips.length > 0 && (
            <div className="w-full max-w-3xl mt-10 grid grid-cols-2 sm:grid-cols-3 gap-4">
              {job.clips.map((clip) => (
                <div key={clip.id} className="rounded-md border border-neutral-800 overflow-hidden">
                  <video
                    className="w-full aspect-[9/16] bg-black"
                    src={`${API_URL}/media/${job.id}/${clip.file_path.split("/").pop()}`}
                    controls
                  />
                  <div className="p-2">
                    <p className="text-sm font-medium truncate">{clip.title}</p>
                    {clip.score !== null && (
                      <p className="text-xs text-neutral-400">Score: {clip.score}</p>
                    )}
                    <div className="mt-2 flex items-center gap-2">
                      <button
                        onClick={() => copyCaptions(clip)}
                        className="rounded border border-neutral-700 px-2 py-1 text-xs text-neutral-300 hover:border-neutral-400"
                      >
                        {copyState?.id === clip.id
                          ? copyState.state === "ok"
                            ? "Copied!"
                            : "Copy failed"
                          : "Copy captions"}
                      </button>
                      <a
                        href={`${API_URL}/api/clips/${clip.id}/captions?format=srt`}
                        className="rounded border border-neutral-700 px-2 py-1 text-xs text-neutral-300 hover:border-neutral-400"
                      >
                        .srt
                      </a>
                      <a
                        href={`${API_URL}/api/clips/${clip.id}/captions?format=ass`}
                        className="rounded border border-neutral-700 px-2 py-1 text-xs text-neutral-300 hover:border-neutral-400"
                      >
                        .ass
                      </a>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}

          <div className="w-full max-w-3xl mt-14">
            <h2 className="text-lg font-semibold mb-3">My clips</h2>
            {historyLoading && history.length === 0 && (
              <p className="text-sm text-neutral-500">Loading history...</p>
            )}
            {!historyLoading && history.length === 0 && (
              <p className="text-sm text-neutral-500">
                No clips yet — submit a URL above to get started.
              </p>
            )}
            {history.length > 0 && (
              <div className="flex flex-col gap-2">
                {history.map((entry) => (
                  <button
                    key={entry.id}
                    onClick={() => openHistoryJob(entry)}
                    className={`text-left rounded-md border px-4 py-2 hover:border-neutral-500 transition-colors ${
                      job?.id === entry.id
                        ? "border-neutral-400 bg-neutral-900"
                        : "border-neutral-800"
                    }`}
                  >
                    <div className="flex items-center justify-between gap-4">
                      <p className="text-sm truncate flex-1">{entry.youtube_url}</p>
                      <span className="text-xs text-neutral-400 capitalize whitespace-nowrap">
                        {entry.status}
                      </span>
                    </div>
                    {entry.created_at && (
                      <p className="text-xs text-neutral-500 mt-1">
                        {new Date(entry.created_at).toLocaleString()}
                      </p>
                    )}
                  </button>
                ))}
              </div>
            )}
          </div>
        </>
      )}
    </main>
  );
}
