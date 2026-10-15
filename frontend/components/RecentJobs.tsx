"use client";

import clsx from "clsx";
import Link from "next/link";
import { useEffect, useState } from "react";
import StatusBadge from "@/components/StatusBadge";
import { api, mediaUrl, type JobSummary } from "@/lib/api";
import { ago, timecode } from "@/lib/format";

/** Grid of recent jobs. Polls faster while something is rendering. */
export default function RecentJobs() {
  const [jobs, setJobs] = useState<JobSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [now, setNow] = useState(0);

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const load = () =>
      api
        .listJobs()
        .then((list) => {
          if (!alive) return;
          setJobs(list);
          setError(null);
          setNow(Date.now());
          const busy = list.some((j) => j.status === "running" || j.status === "queued");
          timer = setTimeout(load, busy ? 3000 : 15000);
        })
        .catch((e: unknown) => {
          if (!alive) return;
          setError(e instanceof Error ? e.message : String(e));
          timer = setTimeout(load, 5000);
        });
    load();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, []);

  return (
    <section aria-labelledby="recent" className="mt-12">
      <div className="mb-4 flex items-baseline justify-between">
        <h2 id="recent" className="text-lg font-semibold tracking-tight">
          Your videos
        </h2>
        {jobs && jobs.length > 0 && <span className="text-sm text-muted">{jobs.length}</span>}
      </div>

      {error && !jobs && <p className="text-sm text-bad">{error}</p>}

      {!jobs && !error && (
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
          {Array.from({ length: 4 }, (_, i) => (
            <div key={i} className="aspect-[3/4] animate-pulse rounded-lg bg-subtle" />
          ))}
        </div>
      )}

      {jobs?.length === 0 && (
        <p className="rounded-lg border border-dashed border-border py-12 text-center text-sm text-muted">
          No videos yet.
        </p>
      )}

      {jobs && jobs.length > 0 && (
        <ul className="grid grid-cols-2 gap-x-4 gap-y-6 sm:grid-cols-3 lg:grid-cols-4">
          {jobs.map((j) => (
            <li key={j.id}>
              <JobCard job={j} now={now} />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function JobCard({ job, now }: { job: JobSummary; now: number }) {
  const portrait = job.aspect === "9:16";
  const poster = job.poster_url ? mediaUrl(job.poster_url) : null;
  return (
    <Link href={`/jobs/${job.id}`} className="group block">
      <div className="relative aspect-[3/4] overflow-hidden rounded-lg border border-border bg-subtle">
        {poster ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={poster}
            alt={job.title ?? job.topic}
            loading="lazy"
            className={clsx("size-full transition-opacity group-hover:opacity-90", portrait ? "object-cover" : "object-contain")}
          />
        ) : (
          <div className="flex size-full flex-col items-center justify-center gap-2 px-6">
            <span className="text-sm tabular-nums text-muted">{Math.round(job.progress * 100)}%</span>
            <div className="h-1 w-full overflow-hidden rounded-full bg-border">
              <div className="h-full bg-accent transition-[width] duration-700" style={{ width: `${job.progress * 100}%` }} />
            </div>
          </div>
        )}
        {job.duration != null && (
          <span className="absolute bottom-2 right-2 rounded bg-black/70 px-1.5 py-0.5 text-[11px] tabular-nums text-white">
            {timecode(job.duration)}
          </span>
        )}
      </div>
      <p className="mt-2 line-clamp-2 text-sm font-medium leading-snug group-hover:underline">{job.title ?? job.topic}</p>
      <div className="mt-1 flex items-center gap-2 text-xs text-muted">
        {job.status !== "done" && <StatusBadge status={job.status} />}
        <span>{now ? ago(job.created_at, now) : " "}</span>
      </div>
    </Link>
  );
}
