"use client";

import { useEffect, useRef, useState } from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

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
  clips: Clip[];
};

export default function Home() {
  const [url, setUrl] = useState("");
  const [language, setLanguage] = useState("");
  const [job, setJob] = useState<Job | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [copyState, setCopyState] = useState<{
    id: number;
    state: "ok" | "error";
  } | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopPolling = () => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  };  const pollJob = (jobId: number) => {
    stopPolling();
    let consecutiveFailures = 0;
    const maxConsecutiveFailures = 3;
    pollRef.current = setInterval(async () => {
      try {
        const res = await fetch(`${API_URL}/api/jobs/${jobId}`);
        if (!res.ok) {
          consecutiveFailures += 1;
          if (consecutiveFailures >= maxConsecutiveFailures) {
            stopPolling();
            setJob((prev) =>
              prev
                ? {
                    ...prev,
                    error:
                      "Unable to load job status. The backend may be unreachable.",                    }
                : null,
            );
            return;
          }
          return;
        }
        consecutiveFailures = 0;
        const data: Job = await res.json();
        setJob(data);
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
                    "Unable to load job status. The backend may be unreachable.",              }
              : null,
            );
          return;
        }
      }
    }, 2500);
  };

  const submit = async () => {
    if (!url) return;
    setSubmitting(true);
    setJob(null);
    try {
      const res = await fetch(`${API_URL}/api/jobs`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
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
      // If the backend is unreachable, don't start a poll that will just
      // produce the same unreachable error again.
      if (
        message === "Could not reach the backend. Is the API running at " +
          API_URL + "?"
      ) {
        return;
      }
      // For server-side create-job rejections (non-2xx), still poll once so
      // we can surface any real job error the API returned.
      const fallbackId = 0;
      pollJob(fallbackId);
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
      <h1 className="text-3xl font-bold mb-2">Mananabas</h1>
      <p className="text-neutral-400 mb-8">
        Paste a YouTube URL, get back ranked vertical clips with captions.
      </p>

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
          <p className="text-sm text-neutral-400">Job #{job.id}</p>
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
    </main>
  );
}
