"""Small, careful HTTP helpers (standard library only).

Everything here has a timeout and a size cap, sends a normal browser User-Agent, follows redirects,
and turns network trouble into UrlError messages a person can act on.
"""

from __future__ import annotations

import gzip
import re
import socket
import ssl
import urllib.error
import urllib.request
import zlib
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from .model import Options, UrlError


@dataclass
class Response:
    url: str  # final URL after redirects
    status: int
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "")

    @property
    def length(self) -> int | None:
        try:
            if "content-range" in self.headers:
                return int(self.headers["content-range"].rsplit("/", 1)[-1])
            return int(self.headers["content-length"])
        except (KeyError, ValueError):
            return None

    def text(self) -> str:
        ct = self.headers.get("content-type", "")
        m = re.search(r"charset=([\w\-]+)", ct, re.I)
        for enc in ([m.group(1)] if m else []) + ["utf-8", "latin-1"]:
            try:
                return self.body.decode(enc)
            except (UnicodeDecodeError, LookupError):
                continue
        return self.body.decode("utf-8", "replace")


def _request(url: str, headers: dict[str, str], method: str = "GET") -> urllib.request.Request:
    return urllib.request.Request(url, headers=headers, method=method)


def base_headers(opts: Options, referer: str = "", extra: dict[str, str] | None = None) -> dict[str, str]:
    h = {
        "User-Agent": opts.user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.8",
        "Accept-Encoding": "gzip, deflate",
    }
    if referer:
        h["Referer"] = referer
    if extra:
        h.update(extra)
    return h


def explain(err: Exception, url: str) -> UrlError:
    """Turn a network exception into something readable."""
    host = urlsplit(url).hostname or url
    if isinstance(err, urllib.error.HTTPError):
        code = err.code
        if code in (401, 403):
            return UrlError(f"{host} refused access ({code}).", "It may need a login, block bots, or be limited to your region.")
        if code == 404:
            return UrlError("That page or file does not exist (404).", "Check the link for typos.")
        if code == 410:
            return UrlError("That link has been removed (410).")
        if code == 429:
            return UrlError(f"{host} says too many requests (429).", "Wait a minute and try again.")
        if code >= 500:
            return UrlError(f"{host} has a problem right now ({code}).", "Try again later.")
        return UrlError(f"The server answered {code}.")
    reason = getattr(err, "reason", err)
    if isinstance(reason, socket.gaierror):
        return UrlError(f"Cannot find {host}.", "Check the address and your internet connection.")
    if isinstance(reason, (socket.timeout, TimeoutError)) or isinstance(err, (socket.timeout, TimeoutError)):
        return UrlError(f"{host} took too long to answer.", "Check your connection and try again.")
    if isinstance(reason, ssl.SSLError) or isinstance(err, ssl.SSLError):
        return UrlError(f"Secure connection to {host} failed.", "The site's certificate may be invalid, or your system's certificates are out of date.", try_http=True)
    if isinstance(reason, ConnectionRefusedError):
        return UrlError(f"{host} refused the connection.", try_http=True)
    return UrlError(f"Could not reach {host}: {reason}")


def _decode(body: bytes, encoding: str) -> bytes:
    enc = (encoding or "").lower()
    try:
        if enc == "gzip":
            return gzip.decompress(body)
        if enc == "deflate":
            try:
                return zlib.decompress(body)
            except zlib.error:
                return zlib.decompress(body, -zlib.MAX_WBITS)
    except (OSError, zlib.error, EOFError):
        return body
    return body


def fetch(
    url: str,
    opts: Options,
    *,
    referer: str = "",
    headers: dict[str, str] | None = None,
    method: str = "GET",
    max_bytes: int = 2_000_000,
    byte_range: tuple[int, int] | None = None,
    timeout: float | None = None,
) -> Response:
    """GET (or HEAD) a URL. Reads at most `max_bytes` of the body."""
    h = base_headers(opts, referer, headers)
    if byte_range:
        h["Range"] = f"bytes={byte_range[0]}-{byte_range[1]}"
        h["Accept-Encoding"] = "identity"
    req = _request(url, h, method)
    try:
        with urllib.request.urlopen(req, timeout=timeout or opts.timeout) as r:
            hdrs = {k.lower(): v for k, v in r.headers.items()}
            body = b"" if method == "HEAD" else r.read(max_bytes)
            if hdrs.get("content-encoding"):
                body = _decode(body, hdrs["content-encoding"])
            return Response(r.geturl(), r.status, hdrs, body)
    except urllib.error.HTTPError as e:
        raise explain(e, url) from None
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise explain(e, url) from None


def probe(url: str, opts: Options, *, referer: str = "", sample: int = 4096) -> Response:
    """Ask what a URL is without downloading it: a tiny ranged GET (works where HEAD is blocked)."""
    try:
        r = fetch(url, opts, referer=referer, byte_range=(0, sample - 1), max_bytes=sample, timeout=min(opts.timeout, 10))
        return r
    except UrlError as first:
        # some servers refuse Range or reject GET probes: try a plain HEAD before giving up
        try:
            return fetch(url, opts, referer=referer, method="HEAD", timeout=min(opts.timeout, 10))
        except UrlError:
            raise first from None


def alive(url: str, opts: Options, *, referer: str = "") -> bool:
    """Does this media URL answer? (Used to throw away dead candidates.)"""
    try:
        r = probe(url, opts, referer=referer, sample=512)
    except UrlError:
        return False
    return r.status < 400
