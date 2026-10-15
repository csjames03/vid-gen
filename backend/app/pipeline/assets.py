"""Stage 2 - Assets: find and download one stock clip/photo per scene.

For each scene we search every query on every provider, score the results and take the
best one that no other scene uses yet. Scenes asking for a video fall back to photos
(and vice versa).

Search responses are cached for 24h (Pixabay's terms ask for this), downloaded media
forever, under data/cache/. Files are hard-linked into the job's assets/ folder.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

import httpx

from app.config import settings
from app.models import AssetManifest, Aspect, JobRequest, MediaType, Scene, SceneAsset, Script
from app.utils import ffmpeg

ASSETS_FILE = "assets.json"
ASSETS_DIR = "assets"
SEARCH_CACHE_TTL = 24 * 3600
USER_AGENT = "vid-gen/0.1 (personal prototype; topic-to-video generator)"
WORDS_PER_SECOND = 2.5  # 150 wpm, same as the script stage
MAX_TRIES_PER_SCENE = 3  # candidates to try if a download is broken

Log = Callable[[str], None]


# --- candidates + providers ---------------------------------------------------------

@dataclass
class Candidate:
    provider: str
    id: str
    kind: MediaType
    url: str  # direct download URL
    width: int
    height: int
    duration: float | None = None
    page_url: str = ""
    author: str = ""
    tags: list[str] = field(default_factory=list)
    thumbnail: str = ""
    query: str = ""
    rank: int = 0  # position in the provider's result list

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.kind}:{self.id}"

    @property
    def ext(self) -> str:
        if self.kind is MediaType.video:
            return ".mp4"
        return (Path(urlparse(self.url).path).suffix or ".jpg").lower()


class Provider(Protocol):
    name: str

    def search(self, query: str, kind: MediaType, aspect: Aspect) -> list[Candidate]: ...


def pick_rendition(renditions: list[dict], target_short: int = 1080) -> dict | None:
    """Prefer the largest rendition whose short side is 720..target; else the smallest
    bigger one (4K is a big download for nothing); else the biggest we have."""
    usable = [r for r in renditions if r.get("url") and r.get("width") and r.get("height")]
    if not usable:
        return None
    short = lambda r: min(r["width"], r["height"])  # noqa: E731
    sweet = [r for r in usable if 720 <= short(r) <= target_short]
    if sweet:
        return max(sweet, key=short)
    bigger = [r for r in usable if short(r) > target_short]
    if bigger:
        return min(bigger, key=short)
    return max(usable, key=short)


class PixabayProvider:
    name = "pixabay"
    API = "https://pixabay.com/api/"

    def __init__(self, api_key: str, http: httpx.Client, cache_dir: Path):
        self.api_key = api_key
        self.http = http
        self.cache_dir = cache_dir / "search" / self.name

    def search(self, query: str, kind: MediaType, aspect: Aspect) -> list[Candidate]:
        params: dict[str, str | int] = {"q": query[:100], "per_page": 20, "safesearch": "true",
                                        "order": "popular"}
        if kind is MediaType.video:
            url = self.API + "videos/"
            params["video_type"] = "film"
        else:
            url = self.API
            params |= {"image_type": "photo", "orientation": aspect.orientation, "min_width": 1000}
        data = self._get(url, params)
        out = []
        for rank, hit in enumerate(data.get("hits", [])):
            c = self._parse_video(hit) if kind is MediaType.video else self._parse_photo(hit)
            if c:
                c.query, c.rank = query, rank
                out.append(c)
        return out

    def _get(self, url: str, params: dict) -> dict:
        key = hashlib.sha1((url + json.dumps(params, sort_keys=True)).encode()).hexdigest()[:16]
        path = self.cache_dir / f"{key}.json"
        if path.exists() and time.time() - path.stat().st_mtime < SEARCH_CACHE_TTL:
            return json.loads(path.read_text(encoding="utf-8"))
        for attempt in range(3):
            r = self.http.get(url, params={**params, "key": self.api_key})
            if r.status_code != 429:
                break
            time.sleep(5 * (attempt + 1))  # rate limited: 100 requests / 60s
        r.raise_for_status()
        data = r.json()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
        return data

    def _parse_video(self, hit: dict) -> Candidate | None:
        r = pick_rendition(list((hit.get("videos") or {}).values()))
        if not r:
            return None
        return Candidate(
            provider=self.name, id=str(hit["id"]), kind=MediaType.video, url=r["url"],
            width=r["width"], height=r["height"], duration=float(hit.get("duration") or 0) or None,
            page_url=hit.get("pageURL", ""), author=hit.get("user", ""),
            tags=_split_tags(hit.get("tags", "")), thumbnail=r.get("thumbnail", ""),
        )

    def _parse_photo(self, hit: dict) -> Candidate | None:
        url = hit.get("largeImageURL")
        w, h = hit.get("imageWidth") or 0, hit.get("imageHeight") or 0
        if not url or not w or not h:
            return None
        scale = min(1.0, 1280 / max(w, h))  # largeImageURL is at most 1280px on the long side
        return Candidate(
            provider=self.name, id=str(hit["id"]), kind=MediaType.image, url=url,
            width=round(w * scale), height=round(h * scale),
            page_url=hit.get("pageURL", ""), author=hit.get("user", ""),
            tags=_split_tags(hit.get("tags", "")), thumbnail=hit.get("previewURL", ""),
        )


def _split_tags(tags: str) -> list[str]:
    return [t.strip().lower() for t in tags.split(",") if t.strip()]


# --- scoring + selection -------------------------------------------------------------
#
# Pixabay matches queries loosely: a fairground ride tagged "fair, folk festival, octopus"
# comes back for "octopus". Tags are ordered by importance, so we check that the query
# words are all in the tags, that the *leading* tags are about the scene, and drop
# anything under MIN_RELEVANCE (a relevant photo beats an off-topic video).

def estimate_duration(scene: Scene) -> float:
    return round(max(2.0, len(scene.narration.split()) / WORDS_PER_SECOND), 1)


MIN_RELEVANCE = 5.0
LEAD_TAGS = 2
_STOPWORDS = set("""a an the and or of in on at to for with from by as is are was were be been it its
this that these those their them they he she his her our your you we i not no but so than then
into onto over under up down out about after before very just can will one two three all any
more most some such only also each every which who what when where how why""".split())


def _stem(w: str) -> str:
    """Crude plural -> singular, enough to match tags: octopuses, reefs, berries, boxes."""
    if len(w) <= 3 or not w.endswith("s") or w.endswith("ss"):
        return w
    if w.endswith("ies"):
        return w[:-3] + "y"
    if w.endswith(("ses", "xes", "ches", "shes", "zes")):
        return w[:-2]
    return w[:-1]


def words_of(text: str) -> set[str]:
    return {_stem(w) for w in re.findall(r"[a-z]+", text.lower()) if w not in _STOPWORDS and len(w) > 2}


def scene_context(scene: Scene, topic: str) -> set[str]:
    return words_of(" ".join([topic, scene.visual_description, scene.narration, *scene.search_queries]))


def topic_anchor(script: Script, topic: str, min_share: float = 0.4) -> set[str]:
    """Topic words that most scenes talk about, e.g. {"coffee"} for "how coffee changed the
    world". Used to keep vague queries ("exploration") on topic."""
    per_scene = [words_of(" ".join([s.narration, *s.search_queries])) for s in script.scenes]
    return {w for w in words_of(topic)
            if sum(w in ws for ws in per_scene) >= max(1, min_share * len(per_scene))}


def relevance(c: Candidate, context: set[str], anchor: set[str] = frozenset()) -> float:
    query = words_of(c.query)
    tag_words = [words_of(t) for t in c.tags]
    all_tags = set().union(*tag_words) if tag_words else set()
    if not query or not query <= all_tags:
        return 0.0  # some query word isn't in the tags at all
    r = 4.0
    lead = set().union(*tag_words[:LEAD_TAGS]) if tag_words else set()
    if lead & (context | query):
        r += 2.0  # the clip is *about* this, not just tagged with it
    r += 0.5 * min(4, len(set().union(*tag_words[:10]) & context))
    if anchor & all_tags:
        r += 1.5  # on topic, not just on the (possibly vague) query
    return r


def score(c: Candidate, aspect: Aspect, est_duration: float, query_index: int,
          context: set[str], anchor: set[str] = frozenset()) -> tuple[float, float]:
    """Returns (total score, relevance part)."""
    rel = relevance(c, context, anchor)
    s = rel - 1.0 * query_index - 0.15 * c.rank  # earlier queries are more specific
    if (c.height > c.width) == (aspect is Aspect.portrait):
        s += 2.0  # no heavy crop needed
    if min(c.width, c.height) < 720:
        s -= 3.0
    if c.kind is MediaType.video and c.duration:
        if c.duration >= est_duration:
            s += 1.5
        else:  # will have to loop / slow down
            s -= 3.0 * (1 - c.duration / est_duration)
        if c.duration > 60:
            s -= 1.0  # long download, we only need a few seconds
    return s, rel


def rank_candidates(scene: Scene, kind: MediaType, providers: list[Provider], aspect: Aspect,
                    used: set[str], log: Log, topic: str = "",
                    anchor: set[str] = frozenset()) -> list[Candidate]:
    est = estimate_duration(scene)
    context = scene_context(scene, topic)
    queries = list(scene.search_queries)
    anchor_query = " ".join(sorted(anchor))
    if anchor_query and anchor_query not in queries:
        queries.append(anchor_query)  # last resort: just the topic's main subject
    scored: dict[str, tuple[float, Candidate]] = {}
    for qi, query in enumerate(queries):
        for p in providers:
            try:
                results = p.search(query, kind, aspect)
            except httpx.HTTPError as e:
                log(f"  {p.name} search failed for {query!r}: {e}")
                continue
            for c in results:
                if c.key in used:
                    continue
                sc, rel = score(c, aspect, est, qi, context, anchor)
                if rel < MIN_RELEVANCE:
                    continue
                if c.key not in scored or sc > scored[c.key][0]:
                    scored[c.key] = (sc, c)
    return [c for _, c in sorted(scored.values(), key=lambda x: x[0], reverse=True)]


# --- download -------------------------------------------------------------------------

def download(http: httpx.Client, c: Candidate, cache_dir: Path) -> Path:
    path = cache_dir / "media" / c.provider / f"{c.kind}_{c.id}_{c.width}x{c.height}{c.ext}"
    if path.exists() and path.stat().st_size > 0:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    last_err: Exception | None = None
    for _ in range(2):
        try:
            with http.stream("GET", c.url, follow_redirects=True, timeout=120) as r:
                r.raise_for_status()
                with tmp.open("wb") as f:
                    for chunk in r.iter_bytes(1 << 16):
                        f.write(chunk)
            os.replace(tmp, path)
            return path
        except httpx.HTTPError as e:
            last_err = e
    tmp.unlink(missing_ok=True)
    raise last_err  # type: ignore[misc]


def link_into_job(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.unlink(missing_ok=True)
    try:
        os.link(src, dest)  # same drive: no extra disk space
    except OSError:
        shutil.copy2(src, dest)


# --- stage entry points ----------------------------------------------------------------

def make_http() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30, follow_redirects=True)


def default_providers(http: httpx.Client) -> list[Provider]:
    providers: list[Provider] = []
    if settings.pixabay_api_key:
        providers.append(PixabayProvider(settings.pixabay_api_key, http, settings.cache_dir))
    return providers


def fetch_scene(scene: Scene, providers: list[Provider], aspect: Aspect, used: set[str],
                http: httpx.Client, job_dir: Path, log: Log, topic: str = "",
                anchor: set[str] = frozenset()) -> SceneAsset:
    est = estimate_duration(scene)
    other = MediaType.image if scene.media_type is MediaType.video else MediaType.video
    for kind in (scene.media_type, other):
        ranked = rank_candidates(scene, kind, providers, aspect, used, log, topic, anchor)
        for c in ranked[:MAX_TRIES_PER_SCENE]:
            try:
                cached = download(http, c, settings.cache_dir)
                info = ffmpeg.probe(cached)  # also catches broken / non-media downloads
            except (httpx.HTTPError, ffmpeg.FFmpegError) as e:
                log(f"  scene {scene.id}: skipping {c.key} ({type(e).__name__})")
                used.add(c.key)
                continue
            rel = f"{ASSETS_DIR}/scene_{scene.id:02d}{c.ext}"
            link_into_job(cached, job_dir / rel)
            used.add(c.key)
            dur = round(info.duration, 2) if c.kind is MediaType.video else None
            log(f"Scene {scene.id}: {c.provider} {c.kind} {c.id} {info.width}x{info.height}"
                + (f" {dur}s" if dur else "") + f" (need ~{est}s) via {c.query!r}")
            return SceneAsset(
                scene_id=scene.id, file=rel, kind=c.kind, source=c.provider, source_id=c.id,
                query=c.query, width=info.width or c.width, height=info.height or c.height,
                duration=dur, est_duration=est, page_url=c.page_url, author=c.author,
                thumbnail=c.thumbnail,
            )
        if ranked:
            break  # had candidates of the preferred kind but all failed to download
    log(f"Scene {scene.id}: no asset found for {scene.search_queries}")
    return SceneAsset(scene_id=scene.id, est_duration=est)


def run(job_dir: Path, req: JobRequest, script: Script, log: Log = print,
        providers: list[Provider] | None = None, http: httpx.Client | None = None) -> AssetManifest:
    own_http = http is None
    http = http or make_http()
    try:
        providers = default_providers(http) if providers is None else providers
        if not providers:
            log("No media providers configured (set PIXABAY_API_KEY in .env)")
        used: set[str] = set()
        anchor = topic_anchor(script, req.topic)
        log(f"Topic anchor: {sorted(anchor) or 'none'}")
        assets = [fetch_scene(sc, providers, req.aspect, used, http, job_dir, log, req.topic, anchor)
                  for sc in script.scenes]
    finally:
        if own_http:
            http.close()
    manifest = AssetManifest(scenes=assets)
    (job_dir / ASSETS_FILE).write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    found = len(assets) - len(manifest.missing)
    log(f"Assets ready: {found}/{len(assets)} scenes" + (f", missing {manifest.missing}" if manifest.missing else ""))
    return manifest


def load(job_dir: Path) -> AssetManifest:
    return AssetManifest.model_validate_json((job_dir / ASSETS_FILE).read_text(encoding="utf-8"))
