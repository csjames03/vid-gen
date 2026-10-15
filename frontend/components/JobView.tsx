"use client";

import { ArrowLeft, DownloadSimple, Trash } from "@phosphor-icons/react";
import clsx from "clsx";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { use, useEffect, useState } from "react";
import LogConsole from "@/components/LogConsole";
import SceneCard from "@/components/SceneCard";
import StageTimeline, { STAGE_INFO } from "@/components/StageTimeline";
import StatusBadge from "@/components/StatusBadge";
import { api, mediaUrl, STAGES, type JobDetail, type SceneView, type Stage } from "@/lib/api";
import { timecode } from "@/lib/format";
import { useJobEvents } from "@/lib/useJobEvents";

const REDO: { stage: Stage; label: string }[] = [
  { stage: "script", label: "New script" },
  { stage: "assets", label: "New footage" },
  { stage: "voice", label: "New voice" },
  { stage: "render", label: "Re-render" },
];

export default function JobView({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const router = useRouter();
  const { job, error, notFound, reconnect } = useJobEvents(id);
  const [scenes, setScenes] = useState<SceneView[]>([]);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  // scene cards fill in as the script, footage and voice stages finish
  const sceneKey = job ? `${job.stages.script}|${job.stages.assets}|${job.stages.voice}` : "";
  useEffect(() => {
    if (!sceneKey.startsWith("done")) return;
    api.scenes(id).then(setScenes).catch(() => {});
  }, [id, sceneKey]);

  async function redo(stage?: Stage) {
    setBusy(true);
    setActionError(null);
    try {
      await api.retryJob(id, stage);
      reconnect();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!confirm("Delete this video and all its files?")) return;
    try {
      await api.deleteJob(id);
      router.push("/");
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e));
    }
  }

  if (notFound)
    return (
      <div className="py-24 text-center">
        <p className="text-lg font-semibold">Video not found</p>
        <Link href="/" className="mt-2 inline-block text-sm text-muted underline underline-offset-2 hover:text-fg">
          Back to all videos
        </Link>
      </div>
    );
  if (!job) return <JobSkeleton error={error} />;

  const active = job.status === "running" || job.status === "queued";
  const portrait = job.request.aspect === "9:16";

  return (
    <div className="pt-6">
      <Link href="/" className="mb-5 inline-flex items-center gap-1.5 text-sm text-muted hover:text-fg">
        <ArrowLeft size={14} /> All videos
      </Link>

      <header className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <h1 className="text-2xl font-semibold leading-tight tracking-tight">{job.title ?? job.request.topic}</h1>
          <div className="mt-1.5 flex flex-wrap items-center gap-x-2 text-sm text-muted">
            <StatusBadge status={job.status} />
            <span aria-hidden>·</span>
            <span>
              {job.request.duration_sec}s, {portrait ? "vertical" : "wide"}, {job.request.tone}
            </span>
          </div>
          {job.title && <p className="mt-1 text-sm text-muted">Topic: {job.request.topic}</p>}
        </div>
        {!active && (
          <div className="flex shrink-0 gap-2">
            {job.status === "done" && (
              <a
                href={mediaUrl(`/jobs/${id}/video?download=true`)}
                className="inline-flex h-9 items-center gap-2 rounded-md bg-fg px-3.5 text-sm font-medium text-bg hover:opacity-85"
              >
                <DownloadSimple /> Download
              </a>
            )}
            <button
              type="button"
              onClick={remove}
              aria-label="Delete video"
              title="Delete video"
              className="inline-flex size-9 items-center justify-center rounded-md border border-border text-muted transition-colors hover:border-bad hover:text-bad"
            >
              <Trash />
            </button>
          </div>
        )}
      </header>

      <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_300px]">
        <div className="min-w-0 space-y-6">
          {job.status === "done" && job.output_url ? (
            <div className={clsx("overflow-hidden rounded-lg bg-black", portrait && "mx-auto max-w-[min(100%,380px)]")}>
              <video
                key={job.updated_at}
                src={`${mediaUrl(job.output_url)}?v=${encodeURIComponent(job.updated_at)}`}
                poster={mediaUrl(`/jobs/${id}/poster`)}
                controls
                playsInline
                className={clsx("block w-full", portrait ? "aspect-[9/16]" : "aspect-video")}
              />
            </div>
          ) : job.status === "failed" ? (
            <FailedPanel job={job} onRetry={() => redo()} busy={busy} />
          ) : (
            <Progress job={job} />
          )}

          {!active && (
            <div className="flex flex-wrap items-center gap-2">
              <span className="mr-1 text-sm text-muted">Redo:</span>
              {REDO.map((r) => (
                <button
                  key={r.stage}
                  type="button"
                  disabled={busy}
                  onClick={() => redo(r.stage)}
                  className="h-8 rounded-md border border-border px-3 text-sm transition-colors hover:bg-subtle disabled:opacity-40"
                >
                  {r.label}
                </button>
              ))}
            </div>
          )}
          {actionError && <p role="alert" className="text-sm text-bad">{actionError}</p>}

          {scenes.length > 0 && (
            <section aria-labelledby="scenes">
              <h2 id="scenes" className="text-base font-semibold">
                Scenes <span className="font-normal text-muted">({scenes.length})</span>
              </h2>
              <div className="mt-1 divide-y divide-border">
                {scenes.map((s) => (
                  <SceneCard key={s.id} scene={s} aspect={job.request.aspect} />
                ))}
              </div>
            </section>
          )}
        </div>

        <aside className="space-y-6">
          <section>
            <h2 className="mb-3 text-sm font-semibold">Steps</h2>
            <StageTimeline stages={job.stages} />
          </section>
          <details open={active || job.status === "failed"} className="group">
            <summary className="mb-2 cursor-pointer text-sm font-semibold marker:text-muted">Log</summary>
            <LogConsole lines={job.log} />
          </details>
        </aside>
      </div>
    </div>
  );
}

function Progress({ job }: { job: JobDetail }) {
  const elapsed = useElapsed(job.created_at, job.status === "running" || job.status === "queued");
  const stage = job.stage ?? STAGES.find((s) => job.stages[s] === "pending") ?? "script";
  const index = STAGES.indexOf(stage) + 1;
  const queued = job.status === "queued";
  const lastLine = job.log.at(-1)?.replace(/^\d{2}:\d{2}:\d{2} /, "") ?? "";
  const pct = Math.round(job.progress * 100);

  return (
    <div className="rounded-lg border border-border bg-surface p-5 sm:p-6">
      <div className="flex items-baseline justify-between gap-4">
        <p className="text-lg font-medium">{queued ? "Waiting in queue" : `${STAGE_INFO[stage].doing}…`}</p>
        <span className="text-sm tabular-nums text-muted">{pct}%</span>
      </div>
      <p className="mt-0.5 text-sm text-muted">
        {queued ? `Position ${job.queue_position ?? "?"}` : `Step ${index} of ${STAGES.length}`} · {timecode(elapsed)} elapsed
      </p>
      <div className="mt-4 h-1.5 overflow-hidden rounded-full bg-subtle">
        <div className="h-full rounded-full bg-accent transition-[width] duration-700 ease-out" style={{ width: `${Math.max(2, pct)}%` }} />
      </div>
      {lastLine && <p className="mt-3 truncate font-mono text-xs text-muted">{lastLine}</p>}
      <p className="mt-4 text-xs text-muted">
        Writing the script is the slowest step (1–4 min on CPU). You can leave this page; the job keeps running.
      </p>
    </div>
  );
}

function FailedPanel({ job, onRetry, busy }: { job: JobDetail; onRetry: () => void; busy: boolean }) {
  const failedStage = STAGES.find((s) => job.stages[s] === "failed");
  return (
    <div className="rounded-lg border border-bad/40 bg-surface p-5 sm:p-6">
      <p className="text-lg font-medium text-bad">
        Failed{failedStage ? ` at ${STAGE_INFO[failedStage].title.toLowerCase()}` : ""}
      </p>
      <pre className="scroll-thin mt-3 max-h-40 overflow-auto whitespace-pre-wrap rounded-md bg-subtle p-3 font-mono text-xs">{job.error}</pre>
      <button
        type="button"
        onClick={onRetry}
        disabled={busy}
        className="mt-4 h-9 rounded-md bg-fg px-3.5 text-sm font-medium text-bg hover:opacity-85 disabled:opacity-40"
      >
        Resume from where it stopped
      </button>
    </div>
  );
}

function JobSkeleton({ error }: { error: string | null }) {
  return (
    <div className="pt-10">
      {error ? (
        <p className="text-sm text-bad">{error}</p>
      ) : (
        <div className="space-y-4">
          <div className="h-8 w-1/2 animate-pulse rounded bg-subtle" />
          <div className="h-64 animate-pulse rounded-lg bg-subtle" />
        </div>
      )}
    </div>
  );
}

function useElapsed(since: string, running: boolean): number {
  const [now, setNow] = useState<number | null>(null);
  useEffect(() => {
    if (!running) return;
    const tick = () => setNow(Date.now());
    const t = setInterval(tick, 1000);
    const first = setTimeout(tick, 0);
    return () => {
      clearInterval(t);
      clearTimeout(first);
    };
  }, [running]);
  return now ? Math.max(0, (now - new Date(since).getTime()) / 1000) : 0;
}
