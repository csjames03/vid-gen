/** 54.2 -> "00:54", 90.3 -> "01:30" */
export function timecode(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return "--:--";
  const s = Math.round(seconds);
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

/** "2026-10-15T01:58:32Z" -> "2 min ago" */
export function ago(iso: string, now: number): string {
  const diff = Math.max(0, (now - new Date(iso).getTime()) / 1000);
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)} min ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} h ago`;
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/** "01:58:32 Script attempt 1/3" -> ["01:58:32", "Script attempt 1/3"] */
export function splitLogLine(line: string): [string, string] {
  const m = /^(\d{2}:\d{2}:\d{2}) ([\s\S]*)$/.exec(line);
  return m ? [m[1], m[2]] : ["", line];
}
