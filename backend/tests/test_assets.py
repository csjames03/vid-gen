import httpx
import pytest

from app.models import Aspect, JobRequest, MediaType, Script
from app.pipeline import assets as ap
from app.utils import ffmpeg


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    d = tmp_path_factory.mktemp("srv")
    ffmpeg.run(["-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=1",
                "-c:v", "libx264", "-preset", "ultrafast", "v.mp4"], cwd=d)
    ffmpeg.run(["-f", "lavfi", "-i", "testsrc2=size=800x1200", "-frames:v", "1", "p.jpg"], cwd=d)
    return {"video": (d / "v.mp4").read_bytes(), "image": (d / "p.jpg").read_bytes()}


@pytest.fixture
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(ap.settings, "data_dir", tmp_path / "data")
    return tmp_path / "data" / "cache"


def http_serving(media):
    def handler(request: httpx.Request) -> httpx.Response:
        if "broken" in request.url.path:
            return httpx.Response(200, content=b"not a video")
        if "missing" in request.url.path:
            return httpx.Response(404)
        kind = "image" if request.url.path.endswith(".jpg") else "video"
        return httpx.Response(200, content=media[kind])
    return httpx.Client(transport=httpx.MockTransport(handler))


def cand(id, kind=MediaType.video, w=1920, h=1080, dur=20.0, path=None, tags=None):
    ext = ".jpg" if kind is MediaType.image else ".mp4"
    return ap.Candidate(provider="fake", id=str(id), kind=kind, url=f"https://x.test/{path or id}{ext}",
                        width=w, height=h, duration=dur if kind is MediaType.video else None,
                        tags=tags or ["octopus", "reef", "underwater"])


class FakeProvider:
    name = "fake"

    def __init__(self, videos=(), images=()):
        self.videos, self.images = list(videos), list(images)
        self.calls = []

    def search(self, query, kind, aspect):
        self.calls.append((query, kind))
        src = self.videos if kind is MediaType.video else self.images
        out = []
        for i, c in enumerate(src):
            c2 = ap.Candidate(**{**c.__dict__})
            c2.query, c2.rank = query, i
            out.append(c2)
        return out


def make_script(n=3, media_type="video"):
    return Script.model_validate({
        "title": "t", "hook": "h", "music_mood": "calm",
        "scenes": [{"id": i, "narration": " ".join(["w"] * 25), "visual_description": "x",
                    "search_queries": ["octopus", "reef"], "media_type": media_type}
                   for i in range(1, n + 1)],
    })


def test_pick_rendition_prefers_1080_then_smallest_bigger():
    r = lambda w, h: {"url": "u", "width": w, "height": h}  # noqa: E731
    assert ap.pick_rendition([r(3840, 2160), r(1920, 1080), r(1280, 720), r(640, 360)]) == r(1920, 1080)
    assert ap.pick_rendition([r(3840, 2160), r(2560, 1440)]) == r(2560, 1440)
    assert ap.pick_rendition([r(640, 360), {"url": "", "width": 0, "height": 0}]) == r(640, 360)
    assert ap.pick_rendition([]) is None


def test_score_prefers_matching_orientation_and_length():
    ctx = {"octopus", "reef"}
    v_land = cand(1, w=1920, h=1080, dur=20)
    v_port = cand(2, w=1080, h=1920, dur=20)
    v_short = cand(3, w=1080, h=1920, dur=3)
    for c in (v_land, v_port, v_short):
        c.query = "octopus"
    s = lambda c, a=Aspect.portrait, qi=0: ap.score(c, a, 10, qi, ctx)[0]  # noqa: E731
    assert s(v_port) > s(v_land)
    assert s(v_port) > s(v_short)
    assert s(v_land, Aspect.landscape) > s(v_land, Aspect.landscape, 2)


def test_relevance_rejects_loosely_tagged_results():
    ctx = ap.scene_context(make_script(3).scenes[0], "why octopuses are basically aliens")
    real = cand(1, tags=["octopus", "underwater", "sea", "ocean"])
    ride = cand(2, tags=["fair", "folk festival", "octopus", "carousel", "ride", "fairground"])
    sniper = cand(3, tags=["sniper", "rifle", "camouflage"])
    real.query = ride.query = "octopus"
    sniper.query = "octopus camouflage"
    assert ap.relevance(real, ctx) >= ap.MIN_RELEVANCE
    assert ap.relevance(ride, ctx) < ap.MIN_RELEVANCE
    assert ap.relevance(sniper, ctx) == 0
    assert ap.words_of("Octopuses, the reefs and berries in boxes!") == {"octopus", "reef", "berry", "box"}


def test_scenes_never_reuse_a_clip(tmp_path, media, isolated_cache):
    prov = FakeProvider(videos=[cand(1), cand(2), cand(3), cand(4)])
    with http_serving(media) as http:
        m = ap.run(tmp_path, JobRequest(topic="octopus"), make_script(3), log=lambda m: None,
                   providers=[prov], http=http)
    ids = [a.source_id for a in m.scenes]
    assert len(set(ids)) == 3 and not m.missing
    assert (tmp_path / "assets" / "scene_01.mp4").exists()
    assert m.scenes[0].duration == pytest.approx(1.0, abs=0.1)  # real probed duration
    assert ap.load(tmp_path).scenes[2].file == "assets/scene_03.mp4"


def test_broken_downloads_are_skipped(tmp_path, media, isolated_cache):
    prov = FakeProvider(videos=[cand(1, path="broken"), cand(2, path="missing"), cand(3)])
    with http_serving(media) as http:
        m = ap.run(tmp_path, JobRequest(topic="octopus"), make_script(3), log=lambda m: None,
                   providers=[prov], http=http)
    assert m.scenes[0].source_id == "3"
    assert m.missing == [2, 3]  # broken ones were marked used, nothing left


def test_falls_back_to_other_media_type_then_missing(tmp_path, media, isolated_cache):
    prov = FakeProvider(videos=[], images=[cand(9, kind=MediaType.image, w=1080, h=1620)])
    with http_serving(media) as http:
        m = ap.run(tmp_path, JobRequest(topic="octopus"), make_script(3), log=lambda m: None,
                   providers=[prov], http=http)
    assert m.scenes[0].kind is MediaType.image and m.scenes[0].file == "assets/scene_01.jpg"
    assert m.missing == [2, 3]  # only one image and it's already used


def test_pixabay_parsing_and_search_cache(tmp_path):
    calls = []

    def handler(request):
        calls.append(request.url)
        assert request.url.params["key"] == "k"
        if "/videos/" in request.url.path:
            return httpx.Response(200, json={"hits": [{
                "id": 42, "pageURL": "https://pixabay.com/videos/id-42/", "user": "alice",
                "tags": "octopus, sea, Underwater", "duration": 17,
                "videos": {"large": {"url": "", "width": 0, "height": 0},
                           "medium": {"url": "https://cdn/m.mp4", "width": 1920, "height": 1080,
                                      "thumbnail": "https://cdn/t.jpg"},
                           "small": {"url": "https://cdn/s.mp4", "width": 1280, "height": 720}}}]})
        return httpx.Response(200, json={"hits": [{
            "id": 7, "largeImageURL": "https://cdn/p.jpg", "imageWidth": 4000, "imageHeight": 6000,
            "pageURL": "p", "user": "bob", "tags": "reef"}]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        p = ap.PixabayProvider("k", http, tmp_path)
        [v] = p.search("octopus", MediaType.video, Aspect.portrait)
        [i] = p.search("reef", MediaType.image, Aspect.portrait)
        p.search("octopus", MediaType.video, Aspect.portrait)  # served from cache
    assert (v.id, v.url, v.width, v.duration, v.author) == ("42", "https://cdn/m.mp4", 1920, 17.0, "alice")
    assert "underwater" in v.tags and v.query == "octopus"
    assert (i.width, i.height, i.ext) == (853, 1280, ".jpg")
    assert len(calls) == 2
    assert calls[1].params["orientation"] == "vertical"


def test_topic_anchor_and_bonus():
    s = Script.model_validate({
        "title": "t", "hook": "h", "music_mood": "calm",
        "scenes": [{"id": i, "narration": n, "visual_description": "x", "search_queries": q}
                   for i, (n, q) in enumerate([
                       ("Coffee was found in Ethiopia.", ["ethiopia"]),
                       ("Coffee houses spread across Europe.", ["european history"]),
                       ("Today the world drinks coffee daily.", ["coffee culture"]),
                       ("It connects people.", ["exploration"])], start=1)],
    })
    anchor = ap.topic_anchor(s, "how coffee changed the world")
    assert anchor == {"coffee"}
    statue = cand(1, tags=["statue", "monument", "european", "history"])
    cafe = cand(2, tags=["coffee", "cafe", "european", "history"])
    statue.query = cafe.query = "european history"
    ctx = ap.scene_context(s.scenes[1], "how coffee changed the world")
    assert ap.relevance(cafe, ctx, anchor) > ap.relevance(statue, ctx, anchor)
