"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, type JobDetail } from "./api";

const FINAL = new Set(["done", "failed"]);

/**
 * Live job state over Server-Sent Events (GET /jobs/{id}/events).
 * The server closes the stream once the job is done/failed; call `reconnect()` after
 * a retry to follow the job again. If the stream drops while the job is still
 * running, we fetch the state once and reconnect after a short pause.
 */
export function useJobEvents(id: string) {
  const [job, setJob] = useState<JobDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [generation, setGeneration] = useState(0);
  const finalRef = useRef(false);

  useEffect(() => {
    let source: EventSource | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    let disposed = false;
    finalRef.current = false;

    const connect = () => {
      source = new EventSource(api.eventsUrl(id));
      source.addEventListener("state", (e) => {
        const data = JSON.parse((e as MessageEvent).data) as JobDetail;
        setJob(data);
        setError(null);
        if (FINAL.has(data.status)) {
          finalRef.current = true;
          source?.close();
        }
      });
      source.addEventListener("deleted", () => {
        source?.close();
        setNotFound(true);
      });
      source.onerror = () => {
        source?.close();
        if (disposed || finalRef.current) return;
        api
          .getJob(id)
          .then((data) => {
            setJob(data);
            if (FINAL.has(data.status)) finalRef.current = true;
            else retryTimer = setTimeout(connect, 2000);
          })
          .catch((err: unknown) => {
            if (err instanceof ApiError && err.status === 404) setNotFound(true);
            else {
              setError(err instanceof Error ? err.message : String(err));
              retryTimer = setTimeout(connect, 4000);
            }
          });
      };
    };

    connect();
    return () => {
      disposed = true;
      source?.close();
      clearTimeout(retryTimer);
    };
  }, [id, generation]);

  const reconnect = useCallback(() => setGeneration((g) => g + 1), []);
  return { job, error, notFound, reconnect };
}
