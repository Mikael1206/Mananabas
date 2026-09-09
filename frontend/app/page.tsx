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
  status: string;
  progress_message: string | null;
  error: string | null;
  clips: Clip[];
};

export default function Home() {
  const [url, setUrl] = useState("");
  const [job, setJob] = useState<Job | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopPolling = () => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  };

  const pollJob = (jobId: number) => {
    stopPolling();
    pollRef.current = setInterval(async () => {
      try {
        const res = await fetch(`${API_URL}/api/jobs/${jobId}`);
        if (!res.ok) return;
        const data: Job = await res.json();
        setJob(data);
        if (data.status === "done" || data.status === "failed") {
          stopPolling();
        }
      } catch (err) {
        console.error("Polling error:", err);
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
        body: JSON.stringify({ youtube_url: url }),
      });
      if (!res.ok) {
        throw new Error(`Failed to create job: ${res.statusText}`);
      }
      const data: Job = await res.json();
      setJob(data);
      pollJob(data.id);
    } catch (error) {
      console.error(error);
      setJob({
        id: 0,
        youtube_url: url,
        status: "failed",
        progress_message: null,
        error: error instanceof Error ? error.message : String(error),
        clips: [],
      });
    } finally {
      setSubmitting(false);
    }
  };

  useEffect(() => stopPolling, []);

  return (
    <main className="min-h-screen flex flex-col items-center px-6 py-16">
      <h1 className="text-3xl font-bold mb-2">Pungol</h1>
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
              </div>
            </div>
          ))}
        </div>
      )}
    </main>
  );
}
