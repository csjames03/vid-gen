import clsx from "clsx";
import type { JobStatus } from "@/lib/api";

const STYLE: Record<JobStatus, string> = {
  queued: "text-muted",
  running: "text-accent",
  done: "text-ok",
  failed: "text-bad",
};

const LABEL: Record<JobStatus, string> = {
  queued: "Queued",
  running: "Generating",
  done: "Done",
  failed: "Failed",
};

export default function StatusBadge({ status, className }: { status: JobStatus; className?: string }) {
  return (
    <span className={clsx("inline-flex items-center gap-1.5 text-xs font-medium", STYLE[status], className)}>
      <span aria-hidden className={clsx("size-1.5 rounded-full bg-current", status === "running" && "animate-pulse")} />
      {LABEL[status]}
    </span>
  );
}
