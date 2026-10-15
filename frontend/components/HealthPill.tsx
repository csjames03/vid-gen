"use client";

import clsx from "clsx";
import { useEffect, useState } from "react";
import { api, type Health } from "@/lib/api";

const LABELS: Record<string, string> = {
  ollama: "Ollama running",
  ollama_model: "LLM model pulled",
  ffmpeg: "FFmpeg installed",
  pixabay_key: "Pixabay API key",
  music: "Music tracks",
  caption_font: "Caption font",
};

/** Header status: is the backend up and is everything it needs available? */
export default function HealthPill() {
  const [health, setHealth] = useState<Health | null>(null);
  const [offline, setOffline] = useState(false);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    let alive = true;
    const check = () =>
      api
        .health()
        .then((h) => alive && (setHealth(h), setOffline(false)))
        .catch(() => alive && setOffline(true));
    check();
    const t = setInterval(check, 30_000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);

  const failing = health ? Object.entries(health.checks).filter(([, ok]) => !ok) : [];
  const state = offline ? "offline" : !health ? "checking" : failing.length ? "degraded" : "ready";
  const text = { offline: "API offline", checking: "Checking…", degraded: `${failing.length} issue${failing.length > 1 ? "s" : ""}`, ready: "Ready" }[state];

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex h-8 items-center gap-2 rounded-md px-2.5 text-sm text-muted transition-colors hover:bg-subtle hover:text-fg"
      >
        <span
          aria-hidden
          className={clsx("size-2 rounded-full", {
            "bg-ok": state === "ready",
            "bg-warn": state === "degraded",
            "bg-bad": state === "offline",
            "bg-muted animate-pulse": state === "checking",
          })}
        />
        {text}
      </button>
      {open && (
        <div className="absolute right-0 top-10 z-50 w-72 rounded-lg border border-border bg-surface p-3 shadow-lg">
          {offline ? (
            <p className="text-sm text-muted">
              Can&apos;t reach the backend. Start it with
              <code className="mt-2 block rounded bg-subtle px-2 py-1.5 font-mono text-xs text-fg">
                cd backend; uv run uvicorn app.main:app --port 8000
              </code>
            </p>
          ) : (
            <ul className="space-y-1.5 text-sm">
              {health &&
                Object.entries(health.checks).map(([key, ok]) => (
                  <li key={key} className="flex items-start gap-2">
                    <span className={clsx("w-3 shrink-0", ok ? "text-ok" : "text-bad")}>{ok ? "✓" : "✕"}</span>
                    <span>
                      {LABELS[key] ?? key}
                      {!ok && health.details[key] && <span className="block text-xs text-muted">{health.details[key]}</span>}
                    </span>
                  </li>
                ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
