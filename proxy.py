"""A local forwarding proxy for hosts whose only request boundary is a base URL.

Some hosts do not expose a hook that receives the assembled request, but they do
let the user point the client at another endpoint — Claude Code honours
``ANTHROPIC_BASE_URL``, for example. Running this module in front of the real
endpoint gives SUTRA the same view of the conversation that ``llm_request``
gives it inside Hermes: the client sends its request here, the request-stage
policy shortens the older recoverable tool results, and the request goes on to
the upstream untouched in every other respect.

What this proxy does not do: it never reads, logs, rewrites or stores a
credential header, never changes the model, the endpoint path or the query, and
never alters a response — it copies the provider's bytes back exactly. It reads
at most the first 256 KiB of an answer to count the token usage the provider
reported, and that reading never feeds back into what the client receives. It
also never keeps a "fallback" copy of the request anywhere — if rewriting fails
for any reason the original bytes are forwarded instead, once.

The proxy is opt-in and local by default: ``127.0.0.1`` only, because a proxy
that accepts unauthenticated traffic on a public interface would be a worse
problem than the one it solves.
"""

from __future__ import annotations

import http.client
import http.server
import json
import re
import ssl
import sys
import urllib.parse
from typing import Any, Callable

from . import config as _config
from . import metrics, prompt as _prompt

#: Path suffixes whose body carries a conversation this package models: the
#: Anthropic Messages endpoint and the OpenAI chat endpoint. A host's base URL
#: may add a prefix (``/provider/v1/chat/completions``), and that prefix belongs
#: to the upstream rather than to the shape, so the match is on the suffix.
#: Everything else is forwarded byte for byte, including
#: ``/v1/messages/count_tokens`` and any endpoint a client adds later.
REWRITE_SUFFIXES = ("/v1/messages", "/chat/completions")


def _rewritable(path: str) -> bool:
    """Whether a request path carries a conversation this package can shorten."""
    return path.endswith(REWRITE_SUFFIXES)

#: Headers that describe one hop rather than the message. They are recomputed
#: (or dropped) instead of being copied, so the upstream sees a valid request.
HOP_HEADERS = frozenset({
    "host", "content-length", "connection", "proxy-connection",
    "transfer-encoding", "upgrade", "keep-alive", "te", "trailer",
})

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787
DEFAULT_UPSTREAM = "https://api.anthropic.com"
DEFAULT_TIMEOUT = 300.0
CHUNK = 65_536

#: A request target ``http.client`` will send to the upstream the operator chose:
#: rooted, not protocol-relative, and free of backslashes, whitespace and control
#: characters. ``//host/path`` and ``http://host/path`` both name another origin,
#: which is why they are refused rather than forwarded.
_SAFE_TARGET = re.compile(r"^/(?!/)[^\\\x00-\x20\x7f]*$")

#: Control characters never belong in a header name or value. A field carrying one
#: is dropped: forwarding it upstream or echoing it back is exactly the splitting
#: the validation exists to prevent.
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _target(path: str) -> str | None:
    """The target to send upstream, or ``None`` when it is not a plain path.

    ``http.client`` treats a target that starts with a scheme as an absolute URL
    and dials *that* host, so a client of this loopback proxy could otherwise make
    SUTRA open a connection to an origin neither the operator nor the client chose.
    """
    return path if _SAFE_TARGET.match(path) else None


def _header_ok(name: str, value: str) -> bool:
    """Whether a header may cross this proxy without risking a split."""
    return bool(name) and not _CONTROL.search(name) and not _CONTROL.search(value)


def _upstream_parts(upstream: str) -> tuple[str, int, str, bool]:
    parts = urllib.parse.urlsplit(upstream)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(f"upstream must be an http(s) URL, got {upstream!r}")
    secure = parts.scheme == "https"
    port = parts.port or (443 if secure else 80)
    base = parts.path.rstrip("/")
    return parts.hostname, port, base, secure


OBSERVE_LIMIT = 262_144
"""How much of one answer is kept for usage extraction.

Usage sits either in a small JSON document or near the start of a stream, so a
bounded prefix covers it without holding a whole answer in memory.
"""

#: Both providers report the same quantities under different names: Anthropic
#: sends ``input_tokens`` and the two cache buckets, OpenAI sends
#: ``prompt_tokens``/``completion_tokens`` and nests the cache hit under
#: ``prompt_tokens_details.cached_tokens``. The counters keep one set of names.
_USAGE_ALIASES = {
    "cache_read_input_tokens": "cache_read_tokens",
    "cache_creation_input_tokens": "cache_write_tokens",
    "prompt_tokens": "input_tokens",
    "completion_tokens": "output_tokens",
}


def _usage_of(payload: Any) -> dict[str, Any] | None:
    """The ``usage`` object of one Anthropic JSON document, if it has one."""
    if not isinstance(payload, dict):
        return None
    usage = payload.get("usage")
    if isinstance(usage, dict):
        return usage
    message = payload.get("message")
    if isinstance(message, dict) and isinstance(message.get("usage"), dict):
        return message["usage"]
    return None


def _usage_from_json(body: bytes) -> dict[str, Any] | None:
    try:
        return _usage_of(json.loads(body))
    except ValueError:
        return None


def _usage_from_stream(body: bytes) -> dict[str, Any] | None:
    """The first usage object in a server-sent-event answer."""
    for line in body.split(b"\n"):
        if not line.startswith(b"data:"):
            continue
        try:
            payload = json.loads(line[5:].strip())
        except ValueError:
            continue
        usage = _usage_of(payload)
        if usage:
            return usage
    return None


def _normalize_usage(usage: dict[str, Any]) -> dict[str, Any]:
    """Map a provider's bucket names onto the ones the counters already use."""
    normalized = {
        _USAGE_ALIASES.get(str(name), str(name)): value
        for name, value in usage.items()
        if isinstance(value, int) and not isinstance(value, bool)
    }
    details = usage.get("prompt_tokens_details")
    if isinstance(details, dict) and "cache_read_tokens" not in normalized:
        cached = details.get("cached_tokens")
        if isinstance(cached, int) and not isinstance(cached, bool):
            normalized["cache_read_tokens"] = cached
    return normalized


def _observe(content_type: str, body: bytes) -> None:
    """Record the usage a provider reported, without touching the answer.

    A provider that does not report usage — or reports it in a shape this
    module does not know — is a normal outcome, not an error: the counters say
    "not reported" rather than inventing a zero.
    """
    usage = None
    if "text/event-stream" in content_type:
        usage = _usage_from_stream(body)
    elif body:
        usage = _usage_from_json(body)
    metrics.record_provider_usage(_normalize_usage(usage) if usage else None)


def rewrite_body(body: bytes, cfg: Any, retain: Callable[[str], str | None],
                 prepare: Callable[..., Any] = _prompt.prepare) -> tuple[bytes, str | None]:
    """Return the body to forward and, if it changed, the reason it did.

    Every failure — undecodable JSON, an unexpected top-level shape, a store
    that declines, a policy bug — returns the original bytes. The function is
    total on purpose: a proxy that can turn a working client into a broken one
    would cost more than it saves.
    """
    if not body or not cfg.prompt_enabled or cfg.mode == "off":
        return body, None
    try:
        request = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return body, None
    if not isinstance(request, dict):
        return body, None
    try:
        decision = prepare(
            request,
            keep_last=_prompt.DEFAULT_KEEP_LAST,
            min_message_bytes=cfg.prompt_min_message_bytes,
            min_request_bytes=cfg.prompt_min_request_bytes,
            max_messages=cfg.prompt_max_messages,
            head_bytes=_prompt.HEAD_BYTES if cfg.prompt_preview else 0,
            tail_bytes=_prompt.TAIL_BYTES if cfg.prompt_preview else 0,
            retain=retain,
        )
    except Exception:  # a policy failure must not break the client's request
        return body, None
    try:
        metrics.record_request_rewrite(decision, mode=cfg.mode)
    except Exception:
        pass
    if not decision.changed or decision.request is None:
        return body, None
    try:
        encoded = json.dumps(decision.request, ensure_ascii=False,
                             separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError):
        return body, None
    if len(encoded) >= len(body):
        return body, None
    return encoded, decision.reason


class Handler(http.server.BaseHTTPRequestHandler):
    """Forward one request at a time; rewrite only a Messages API body."""

    protocol_version = "HTTP/1.1"
    server_version = "sutra-proxy"
    sys_version = ""

    #: Filled in by :func:`make_server`; kept as class attributes so the
    #: stdlib request loop can construct handlers without arguments.
    config: Any = None
    upstream: str = DEFAULT_UPSTREAM
    retain: Callable[[str], str | None] = staticmethod(lambda text: None)
    timeout: float = DEFAULT_TIMEOUT

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003 - stdlib name
        # Deliberately quiet: request paths are fine, bodies and headers are not,
        # and the stdlib format string can include both.
        if self.server.verbose:  # type: ignore[attr-defined]
            super().log_message("%s", "%s" % (fmt % args,))

    # -- request side -------------------------------------------------------
    def _read_body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return b""
        return self.rfile.read(length)

    def _headers_for_upstream(self, body: bytes) -> dict[str, str]:
        headers: dict[str, str] = {}
        for name, value in self.headers.items():
            if name.lower() in HOP_HEADERS or not _header_ok(name, value):
                continue
            headers[name] = value
        headers["Content-Length"] = str(len(body))
        return headers

    def _forward(self, body: bytes, rewritten: bool, *, target: str,
                 send_body: bool = True, observe: bool = False) -> None:
        host, port, base, secure = _upstream_parts(self.upstream)
        path = base + target
        connection: http.client.HTTPConnection
        if secure:
            connection = http.client.HTTPSConnection(
                host, port, timeout=self.timeout,
                context=ssl.create_default_context())
        else:
            connection = http.client.HTTPConnection(host, port, timeout=self.timeout)
        try:
            connection.request(self.command, path, body=body,
                               headers=self._headers_for_upstream(body))
            response = connection.getresponse()
            self._relay(response, rewritten, send_body=send_body,
                       observe=observe)
        finally:
            try:
                connection.close()
            except Exception:
                pass

    def _relay(self, response: http.client.HTTPResponse, rewritten: bool,
               *, send_body: bool = True, observe: bool = False) -> None:
        self.send_response(response.status, response.reason)
        for name, value in response.getheaders():
            if not _header_ok(name, value):
                continue
            lowered = name.lower()
            if lowered in ("transfer-encoding", "connection"):
                continue
            # A HEAD response carries no body, so its declared length is the only
            # description of the body the client asked about: keep it.
            if lowered == "content-length" and send_body:
                continue
            self.send_header(name, value)
        if rewritten:
            self.send_header("X-Sutra-Rewritten", "1")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        if not send_body:
            return
        seen = bytearray()
        try:
            while True:
                chunk = response.read(CHUNK)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
                if observe and len(seen) < OBSERVE_LIMIT:
                    seen.extend(chunk[: OBSERVE_LIMIT - len(seen)])
        except (BrokenPipeError, ConnectionResetError):
            return
        if observe:
            _observe(response.getheader("Content-Type") or "", bytes(seen))

    def _serve(self, *, rewrite: bool = False, read_body: bool = False) -> None:
        """Forward one request, rewriting the body only on a modelled path."""
        target = _target(self.path)
        if target is None:
            self._reject("the request target is not a plain path")
            return
        body = self._read_body() if read_body else b""
        rewritten = False
        observe = _rewritable(urllib.parse.urlsplit(target).path)
        if rewrite and observe:
            new_body, reason = rewrite_body(body, self.config, self.retain)
            if reason is not None:
                body, rewritten = new_body, True
        try:
            self._forward(body, rewritten, target=target,
                          send_body=self.command != "HEAD", observe=observe)
        except (OSError, http.client.HTTPException, ValueError) as exc:
            self._fail(exc)

    def do_POST(self) -> None:  # noqa: N802 - stdlib name
        self._serve(rewrite=True, read_body=True)

    def do_PUT(self) -> None:  # noqa: N802 - stdlib name
        self._serve(read_body=True)

    def do_PATCH(self) -> None:  # noqa: N802 - stdlib name
        self._serve(read_body=True)

    def do_GET(self) -> None:  # noqa: N802 - stdlib name
        self._serve()

    def do_HEAD(self) -> None:  # noqa: N802 - stdlib name
        self._serve()

    def do_DELETE(self) -> None:  # noqa: N802 - stdlib name
        self._serve()

    def do_OPTIONS(self) -> None:  # noqa: N802 - stdlib name
        self._serve()

    def _fail(self, exc: Exception) -> None:
        # The message names the failure class only: an exception text can carry
        # a URL with a token in a query string.
        self._answer(http.client.BAD_GATEWAY,
                     f"the upstream request failed: {type(exc).__name__}")

    def _reject(self, message: str) -> None:
        self._answer(http.client.BAD_REQUEST, message)

    def _answer(self, status: int, message: str) -> None:
        payload = json.dumps({
            "type": "error",
            "error": {"type": "sutra_proxy_error", "message": message},
        }).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(payload)
            self.close_connection = True
        except (BrokenPipeError, ConnectionResetError):
            pass


def make_server(*, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
                upstream: str = DEFAULT_UPSTREAM, verbose: bool = False,
                cfg: Any = None, retain: Callable[[str], str | None] | None = None
                ) -> http.server.HTTPServer:
    """Build the server without starting it, so a caller can choose the thread."""
    resolved = cfg or _config.resolve()
    handler = type("SutraProxyHandler", (Handler,), {
        "config": resolved,
        "upstream": upstream,
        "retain": staticmethod(retain) if retain is not None
                  else staticmethod(_prompt.host_retainer()),
    })
    server = http.server.ThreadingHTTPServer((host, port), handler)
    server.verbose = verbose  # type: ignore[attr-defined]
    return server


def run(argv: list[str] | None = None) -> int:
    """``sutra proxy``: keep the process in the foreground and serve until stopped."""
    args = list(argv or [])
    host, port, upstream, verbose = DEFAULT_HOST, DEFAULT_PORT, DEFAULT_UPSTREAM, False
    index = 0
    while index < len(args):
        flag = args[index]
        if flag in ("--host", "--port", "--upstream") and index + 1 < len(args):
            value = args[index + 1]
            if flag == "--host":
                host = value
            elif flag == "--upstream":
                upstream = value
            else:
                try:
                    port = int(value)
                except ValueError:
                    print(f"sutra proxy: --port needs a number, got {value!r}", file=sys.stderr)
                    return 2
            index += 2
            continue
        if flag == "--verbose":
            verbose = True
            index += 1
            continue
        print(f"sutra proxy: unknown argument {flag!r}", file=sys.stderr)
        return 2
    try:
        server = make_server(host=host, port=port, upstream=upstream, verbose=verbose)
    except (OSError, ValueError) as exc:
        print(f"sutra proxy: cannot listen on {host}:{port}: {type(exc).__name__}", file=sys.stderr)
        return 1
    bound = server.server_address
    print(f"sutra proxy: listening on http://{bound[0]}:{bound[1]} -> {upstream}", flush=True)
    print("point the host at that address, for example "
          f"ANTHROPIC_BASE_URL=http://{bound[0]}:{bound[1]}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def main(argv: list[str] | None = None) -> int:
    return run(sys.argv[1:] if argv is None else argv)


if __name__ == "__main__":  # pragma: no cover - convenience only
    raise SystemExit(main())
