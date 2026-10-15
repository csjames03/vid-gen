import { Question } from "@phosphor-icons/react";
import clsx from "clsx";
import { mediaUrl, type Aspect, type SceneView } from "@/lib/api";
import { timecode } from "@/lib/format";

export default function SceneCard({ scene, aspect }: { scene: SceneView; aspect: Aspect }) {
  const a = scene.asset;
  return (
    <article className="flex gap-3 py-3">
      <div
        className={clsx(
          "shrink-0 overflow-hidden rounded-md bg-subtle",
          aspect === "9:16" ? "h-28 w-[63px]" : "h-[63px] w-28",
        )}
      >
        {scene.thumbnail_url ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={mediaUrl(scene.thumbnail_url)} alt={scene.visual_description} loading="lazy" className="size-full object-cover" />
        ) : (
          <div className="flex size-full items-center justify-center text-muted">
            <Question size={18} />
          </div>
        )}
      </div>
      <div className="min-w-0 flex-1">
        <p className="text-xs text-muted">
          Scene {scene.id}
          {scene.audio_duration != null && ` · ${timecode(scene.audio_duration)}`}
        </p>
        <p className="mt-0.5 line-clamp-3 text-sm leading-snug">{scene.narration}</p>
        <p className="mt-1.5 text-xs text-muted">
          {a?.file ? a.kind : "No footage"}
          {a?.query && <> for “{a.query}”</>}
          {a?.page_url && (
            <>
              {" · "}
              <a href={a.page_url} target="_blank" rel="noreferrer" className="underline underline-offset-2 hover:text-fg">
                {a.source}
                {a.author ? `, ${a.author}` : ""}
              </a>
            </>
          )}
        </p>
      </div>
    </article>
  );
}
