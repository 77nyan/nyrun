"""The engine: picks the strategies in the right order for each URL."""

from __future__ import annotations

import re

from . import detect, ytdlp
from .model import Options, Resolved, UrlError
from .strategies import REGISTRY, Context


class Engine:
    """Resolve URLs. `order` is the default list of strategy names; tweak it to extend or reorder."""

    order = ["direct", "probe", "page", "yt-dlp", "vlc"]

    def __init__(self, opts: Options | None = None) -> None:
        self.opts = opts or Options()

    def plan(self, url: str) -> list[str]:
        """Which strategies will run, in order, for this (already normalised) URL."""
        names = list(self.order)
        policy = self.opts.ytdlp
        if policy == "never":
            return [n for n in names if n != "yt-dlp"]
        if "yt-dlp" not in names:
            return names
        early = policy == "always" or (
            detect.kind_from_url(url) is None and (detect.is_known_host(url) or ytdlp.has_specific_extractor(url))
        )
        if early and detect.kind_from_url(url) is None:
            names.remove("yt-dlp")
            # after "direct" (an obvious file never needs yt-dlp), before the page scan
            names.insert(names.index("direct") + 1 if "direct" in names else 0, "yt-dlp")
        return names

    def resolve(self, text: str) -> Resolved:
        url = detect.normalize_url(text)
        ctx = Context(url, self.opts, assumed_https=not re.match(r"^\s*[\"'<]*[a-z][a-z0-9+.\-]*://", text or "", re.I))
        for name in self.plan(url):
            strat = REGISTRY.get(name)
            if strat is None:
                continue
            try:
                res = strat().resolve(ctx)
            except UrlError as e:
                ctx.fail(e)
                continue
            if res is not None:
                return self._finish(res, ctx)
        if ctx.last_error is not None:
            raise ctx.last_error
        raise UrlError("NyRun could not find a video at that link.", "Try opening the page in your browser and copying the video's own address.")

    @staticmethod
    def _finish(res: Resolved, ctx: Context) -> Resolved:
        extra = " · ".join(n for n in ctx.notes if n and n not in res.note)
        title = res.title
        if title and len(title) > 200:
            title = title[:197] + "..."
        if extra:
            res = Resolved(**{**res.__dict__, "note": f"{res.note} · {extra}" if res.note else extra, "title": title})
        elif title != res.title:
            res = Resolved(**{**res.__dict__, "title": title})
        return res


def resolve(text: str, opts: Options | None = None) -> Resolved:
    return Engine(opts).resolve(text)
