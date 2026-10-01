"""Minimal stdlib JSON-over-HTTPS transport for native adapters.

Native providers (YandexGPT, GigaChat) do not speak the OpenAI dialect, so
the `openai` SDK buys nothing there. This module keeps them dependency-free:
plain urllib with a small injectable seam for tests.

Contract (the seam — what a transport callable must satisfy):

    transport(url, payload, headers, timeout) -> (status_code: int, body: dict)

    - `payload` is a dict (serialized as JSON) or a str/bytes (sent raw —
      used for x-www-form-urlencoded OAuth bodies). The CALLER must set the
      matching Content-Type header; the transport adds nothing.
    - HTTP error statuses (>= 400) are RETURNED, not raised — adapters map
      them onto the ProviderError hierarchy.
    - Network-level failures (DNS, timeout, TLS) raise TransportError with
      `reason` = "network" | "cert" so adapters can hint at TLS fixes.
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request

__all__ = ["JsonTransport", "TransportError"]


class TransportError(Exception):
    """Network-level transport failure.

    `reason`:
        "network" — DNS, connect, timeout, reset: worth retrying later
        "cert"    — TLS certificate verification failed: a config problem,
                    retrying the identical request will not help
    """

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(message)


def _classify_url_error(exc: urllib.error.URLError) -> TransportError:
    reason = getattr(exc, "reason", None)
    if isinstance(reason, ssl.SSLCertVerificationError):
        return TransportError("cert", str(reason))
    return TransportError("network", f"{type(exc).__name__}: {exc}")


def _urllib_post(
    url: str,
    payload,
    headers: dict,
    timeout: float,
    ssl_context: ssl.SSLContext | None,
) -> tuple[int, dict]:
    if isinstance(payload, (bytes, bytearray)):
        data = bytes(payload)
    elif isinstance(payload, str):
        data = payload.encode("utf-8")
    else:
        data = json.dumps(payload).encode("utf-8")

    request = urllib.request.Request(url, data=data, method="POST")
    for key, value in headers.items():
        request.add_header(key, value)

    try:
        with urllib.request.urlopen(
            request, timeout=timeout, context=ssl_context
        ) as response:
            status = int(getattr(response, "status", None) or response.getcode())
            raw = response.read()
    except urllib.error.HTTPError as exc:
        # Error statuses carry a JSON document (Yandex/GigaChat style) — or junk.
        try:
            body = json.loads(exc.read().decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            body = {}
        return int(exc.code), body if isinstance(body, dict) else {}
    except ssl.SSLCertVerificationError as exc:
        raise TransportError("cert", str(exc)) from exc
    except urllib.error.URLError as exc:
        raise _classify_url_error(exc) from exc
    except TimeoutError as exc:  # socket.timeout is TimeoutError on py>=3.10
        raise TransportError("network", f"timeout after {timeout}s: {exc}") from exc
    except OSError as exc:
        raise TransportError("network", f"{type(exc).__name__}: {exc}") from exc

    try:
        body = json.loads(raw.decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError):
        body = {}
    return status, body if isinstance(body, dict) else {}


class JsonTransport:
    """Default production transport: stdlib urllib, JSON in / JSON out.

    `ssl_context` lets an adapter control TLS verification (GigaChat
    endpoints are signed by the Russian Trusted CA, absent from default
    Linux stores). None means standard verification.
    """

    def __init__(self, ssl_context: ssl.SSLContext | None = None) -> None:
        self._ssl_context = ssl_context

    def __call__(self, url: str, payload, headers: dict, timeout: float) -> tuple[int, dict]:
        return _urllib_post(url, payload, headers, timeout, self._ssl_context)

    def __repr__(self) -> str:  # pragma: no cover — debugging nicety
        verified = self._ssl_context is None or (
            self._ssl_context.verify_mode != ssl.CERT_NONE
        )
        return f"JsonTransport(verified={verified})"
