"use client";

import { CircleNotch } from "@phosphor-icons/react";
import clsx from "clsx";
import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { api, type Aspect, type Duration, type Tone, type Voice } from "@/lib/api";

const EXAMPLES = [
  "why octopuses are basically aliens",
  "the sinking of the Titanic",
  "how honeybees make honey",
  "why cats purr",
];

export default function CreateForm() {
  const router = useRouter();
  const [topic, setTopic] = useState("");
  const [duration, setDuration] = useState<Duration>(30);
  const [aspect, setAspect] = useState<Aspect>("9:16");
  const [tone, setTone] = useState<Tone>("informative");
  const [voice, setVoice] = useState("");
  const [voices, setVoices] = useState<Voice[]>([]);
  const [captions, setCaptions] = useState(true);
  const [music, setMusic] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.voices().then(setVoices).catch(() => setVoices([]));
  }, []);

  const trimmed = topic.trim();
  const canSubmit = trimmed.length >= 3 && !submitting;

  async function handleSubmit(e?: FormEvent) {
    e?.preventDefault();
    if (!canSubmit) return;
    setSubmitting(true);
    setError(null);
    try {
      const job = await api.createJob({
        topic: trimmed, duration_sec: duration, aspect, tone, voice: voice || null, captions, music,
      });
      router.push(`/jobs/${job.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="rounded-lg border border-border bg-surface">
      <div className="p-4 sm:p-5">
        <label htmlFor="topic" className="mb-1.5 block text-sm font-medium">
          Topic
        </label>
        <textarea
          id="topic"
          value={topic}
          onChange={(e) => setTopic(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) handleSubmit();
          }}
          maxLength={200}
          rows={2}
          placeholder="e.g. why octopuses are basically aliens"
          className="w-full resize-none rounded-md border border-border bg-bg px-3 py-2.5 text-base placeholder:text-muted/70 focus:border-fg focus:outline-none"
        />
        <p className="mt-2 flex flex-wrap items-center gap-x-1 gap-y-1 text-sm text-muted">
          <span className="mr-1">Try:</span>
          {EXAMPLES.map((ex, i) => (
            <span key={ex}>
              <button
                type="button"
                onClick={() => setTopic(ex)}
                className="underline decoration-border underline-offset-[3px] hover:text-fg hover:decoration-fg"
              >
                {ex}
              </button>
              {i < EXAMPLES.length - 1 && <span aria-hidden>,</span>}
            </span>
          ))}
        </p>

        <div className="mt-5 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Field label="Length">
            <Segmented
              value={duration}
              onChange={setDuration}
              options={[30, 60, 90].map((d) => ({ value: d as Duration, label: `${d}s` }))}
            />
          </Field>
          <Field label="Format">
            <Segmented
              value={aspect}
              onChange={setAspect}
              options={[
                { value: "9:16", label: "Vertical" },
                { value: "16:9", label: "Wide" },
              ]}
            />
          </Field>
          <Field label="Tone">
            <Segmented
              value={tone}
              onChange={setTone}
              options={(["fun", "informative", "dramatic"] as Tone[]).map((t) => ({ value: t, label: t }))}
            />
          </Field>
          <Field label="Voice" htmlFor="voice">
            <select
              id="voice"
              value={voice}
              onChange={(e) => setVoice(e.target.value)}
              className="h-9 w-full rounded-md border border-border bg-bg px-2.5 text-sm focus:border-fg focus:outline-none"
            >
              <option value="">Andrew (US, default)</option>
              {voices.map((v) => (
                <option key={v.name} value={v.name}>
                  {v.name.replace(/^[a-z]{2}-[A-Z]{2}-/, "").replace(/Neural$/, "")} ({v.locale}, {v.gender.toLowerCase()})
                </option>
              ))}
            </select>
          </Field>
        </div>

        <div className="mt-4 flex gap-5">
          <Check checked={captions} onChange={setCaptions} label="Captions" />
          <Check checked={music} onChange={setMusic} label="Background music" />
        </div>
      </div>

      <div className="flex flex-col-reverse gap-3 border-t border-border px-4 py-3 sm:flex-row sm:items-center sm:justify-between sm:px-5">
        <p className="text-xs text-muted">Takes about 3–5 minutes. Ctrl + Enter also works.</p>
        <button
          type="submit"
          disabled={!canSubmit}
          className="inline-flex h-9 items-center justify-center gap-2 rounded-md bg-fg px-4 text-sm font-medium text-bg transition-opacity hover:opacity-85 disabled:cursor-not-allowed disabled:opacity-35"
        >
          {submitting && <CircleNotch className="animate-spin" />}
          {submitting ? "Starting…" : "Generate video"}
        </button>
      </div>
      {error && (
        <p role="alert" className="border-t border-border px-4 py-3 text-sm text-bad sm:px-5">
          {error}
        </p>
      )}
    </form>
  );
}

function Field({ label, htmlFor, children }: { label: string; htmlFor?: string; children: ReactNode }) {
  return (
    <div>
      <label htmlFor={htmlFor} className="mb-1.5 block text-sm text-muted">
        {label}
      </label>
      {children}
    </div>
  );
}

function Segmented<T extends string | number>({
  value, onChange, options,
}: { value: T; onChange: (v: T) => void; options: { value: T; label: ReactNode }[] }) {
  return (
    <div role="radiogroup" className="flex h-9 rounded-md border border-border bg-bg p-0.5">
      {options.map((o) => (
        <button
          key={String(o.value)}
          type="button"
          role="radio"
          aria-checked={value === o.value}
          onClick={() => onChange(o.value)}
          className={clsx(
            "flex flex-1 items-center justify-center rounded-[5px] px-2 text-sm capitalize transition-colors",
            value === o.value ? "bg-surface font-medium shadow-sm ring-1 ring-border" : "text-muted hover:text-fg",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

function Check({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <label className="flex cursor-pointer items-center gap-2 text-sm">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="size-4 accent-[var(--fg)]"
      />
      {label}
    </label>
  );
}
