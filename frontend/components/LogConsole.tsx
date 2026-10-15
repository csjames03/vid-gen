"use client";

import clsx from "clsx";
import { useEffect, useRef } from "react";
import { splitLogLine } from "@/lib/format";

/** Mono log tail. Sticks to the bottom unless the user scrolled up to read. */
export default function LogConsole({ lines }: { lines: string[] }) {
  const ref = useRef<HTMLDivElement>(null);
  const stick = useRef(true);

  useEffect(() => {
    const el = ref.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [lines.length]);

  return (
    <div
      ref={ref}
      onScroll={(e) => {
        const el = e.currentTarget;
        stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
      }}
      role="log"
      aria-live="polite"
      className="scroll-thin h-72 overflow-y-auto rounded-lg border border-border bg-subtle p-3 font-mono text-[11.5px] leading-relaxed"
    >
      {lines.map((line, i) => {
        const [time, msg] = splitLogLine(line);
        const heading = msg.startsWith("== ");
        const bad = /FAILED|failed|error/i.test(msg) && !heading;
        return (
          <div key={i} className={clsx("flex gap-3", heading && "mt-2 first:mt-0")}>
            <span className="shrink-0 text-muted/70">{time}</span>
            <span className={clsx("whitespace-pre-wrap break-words", heading ? "font-semibold" : bad ? "text-bad" : "text-fg/80")}>
              {heading ? msg.replaceAll("=", "").trim() : msg}
            </span>
          </div>
        );
      })}
    </div>
  );
}
