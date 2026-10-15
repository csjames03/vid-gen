import { Check, Minus, X } from "@phosphor-icons/react";
import clsx from "clsx";
import { STAGES, type Stage, type StageStatus } from "@/lib/api";

export const STAGE_INFO: Record<Stage, { title: string; doing: string; detail: string }> = {
  script: { title: "Script", doing: "Writing the script", detail: "qwen2.5 on Ollama, locally" },
  assets: { title: "Footage", doing: "Finding footage", detail: "Pixabay stock video + photos" },
  voice: { title: "Voiceover", doing: "Recording voiceover", detail: "edge-tts neural voice" },
  captions: { title: "Captions", doing: "Timing captions", detail: "word-by-word from voice timings" },
  music: { title: "Music", doing: "Picking a soundtrack", detail: "royalty-free, matched to mood" },
  render: { title: "Render", doing: "Rendering the video", detail: "FFmpeg: crop, crossfade, mix, burn-in" },
};

export default function StageTimeline({ stages }: { stages: Record<Stage, StageStatus> }) {
  return (
    <ol className="space-y-3">
      {STAGES.map((s) => {
        const status = stages[s];
        return (
          <li key={s} className="flex gap-3">
            <StepIcon status={status} />
            <div className="min-w-0">
              <p className={clsx("text-sm", status === "pending" ? "text-muted" : status === "failed" ? "text-bad" : "font-medium")}>
                {STAGE_INFO[s].title}
                <span className="sr-only"> — {status}</span>
              </p>
              <p className="text-xs text-muted">{STAGE_INFO[s].detail}</p>
            </div>
          </li>
        );
      })}
    </ol>
  );
}

function StepIcon({ status }: { status: StageStatus }) {
  const base = "mt-0.5 flex size-[18px] shrink-0 items-center justify-center rounded-full";
  if (status === "done")
    return <span className={clsx(base, "bg-ok text-bg")}><Check size={11} weight="bold" /></span>;
  if (status === "failed")
    return <span className={clsx(base, "bg-bad text-bg")}><X size={11} weight="bold" /></span>;
  if (status === "skipped")
    return <span className={clsx(base, "border border-border text-muted")}><Minus size={11} weight="bold" /></span>;
  if (status === "running")
    return (
      <span className={clsx(base, "border-2 border-accent")}>
        <span className="size-1.5 animate-pulse rounded-full bg-accent" />
      </span>
    );
  return <span className={clsx(base, "border border-border")} />;
}
