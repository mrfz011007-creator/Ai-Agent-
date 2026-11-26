from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
import time
from urllib.parse import urljoin, urlsplit, urlunsplit

MAX_URL_CHARS = 2048
MAX_RESPONSE_BYTES = 500_000
MAX_REDIRECTS = 3
REQUEST_TIMEOUT_SECONDS = 10.0
_ALLOWED_CONTENT_TYPES = (
    "text/",
    "application/json",
    "application/xml",
    "application/xhtml+xml",
    "application/rss+xml",
    "application/atom+xml",
)


class WebFetchError(ValueError):
    """A URL or response was rejected by the bounded web reader."""


def _parse_https_url(url: str):
    if not isinstance(url, str) or not url or len(url) > MAX_URL_CHARS:
        raise WebFetchError("URL_INVALID")
    if any(ord(char) < 32 or ord(char) == 127 for char in url):
        raise WebFetchError("URL_CONTROL_CHARACTER_DENIED")
    try:
        parsed = urlsplit(url)
        port = parsed.port
        hostname = parsed.hostname
    except ValueError as error:
        raise WebFetchError("URL_INVALID") from error
    if parsed.scheme.lower() != "https" or not hostname:
        raise WebFetchError("HTTPS_REQUIRED")
    if parsed.username is not None or parsed.password is not None:
        raise WebFetchError("URL_CREDENTIALS_DENIED")
    if port not in (None, 443):
        raise WebFetchError("NON_STANDARD_PORT_DENIED")
    if parsed.fragment:
        parsed = parsed._replace(fragment="")
    try:
        host = hostname.encode("idna").decode("ascii").lower()
    except UnicodeError as error:
        raise WebFetchError("HOSTNAME_INVALID") from error
    if not host or host.endswith(".") or host == "localhost" or host.endswith(".localhost"):
        raise WebFetchError("LOCAL_HOST_DENIED")
    path = parsed.path or "/"
    target = path + (("?" + parsed.query) if parsed.query else "")
    if len(target) > MAX_URL_CHARS:
        raise WebFetchError("URL_TOO_LONG")
    netloc = f"[{host}]" if ":" in host else host
    normalized = urlunsplit(("https", netloc, path, parsed.query, ""))
    return normalized, host, target


def _public_addresses(host: str) -> list[str]:
    try:
        literal = ipaddress.ip_address(host)
        addresses = [str(literal)]
    except ValueError:
        try:
            records = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        except OSError as error:
            raise WebFetchError("DNS_RESOLUTION_FAILED") from error
        addresses = list(dict.fromkeys(record[4][0] for record in records))
    if not addresses:
        raise WebFetchError("DNS_NO_ADDRESS")
    for value in addresses:
        try:
            address = ipaddress.ip_address(value.split("%", 1)[0])
        except ValueError as error:
            raise WebFetchError("DNS_ADDRESS_INVALID") from error
        # Reject the entire answer if even one record is non-public. This
        # prevents a mixed public/private DNS answer from becoming a bypass.
        if not address.is_global or address.is_multicast or address.is_unspecified:
            raise WebFetchError("NON_PUBLIC_ADDRESS_DENIED")
    return addresses


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS connection pinned to a previously validated numeric IP."""

    def __init__(self, host: str, address: str):
        super().__init__(host=host, port=443, timeout=REQUEST_TIMEOUT_SECONDS,
                         context=ssl.create_default_context())
        self._validated_address = address

    def connect(self):
        raw = socket.create_connection(
            (self._validated_address, 443), timeout=self.timeout
        )
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except Exception:
            raw.close()
            raise


def _read_bounded(response, connection, deadline: float) -> bytes:
    chunks = []
    total = 0
    reader = getattr(response, "read1", None)
    if reader is None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise WebFetchError("REQUEST_TIMEOUT")
        sock = getattr(connection, "sock", None)
        if sock is not None:
            sock.settimeout(remaining)
        body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            raise WebFetchError("RESPONSE_TOO_LARGE")
        return body

    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise WebFetchError("REQUEST_TIMEOUT")
        sock = getattr(connection, "sock", None)
        if sock is not None:
            sock.settimeout(remaining)
        chunk = reader(min(65_536, MAX_RESPONSE_BYTES + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > MAX_RESPONSE_BYTES:
            raise WebFetchError("RESPONSE_TOO_LARGE")
    return b"".join(chunks)


def fetch_url(url: str) -> dict:
    """Fetch one public HTTPS text page without credentials, cookies or writes."""
    current = url
    for redirect_count in range(MAX_REDIRECTS + 1):
        deadline = time.monotonic() + REQUEST_TIMEOUT_SECONDS
        normalized, host, target = _parse_https_url(current)
        addresses = _public_addresses(host)
        if time.monotonic() >= deadline:
            raise WebFetchError("REQUEST_TIMEOUT")
        connection = None
        response = None
        last_error = None
        for address in addresses:
            try:
                connection = _PinnedHTTPSConnection(host, address)
                connection.timeout = max(0.1, deadline - time.monotonic())
                connection.request(
                    "GET",
                    target,
                    headers={
                        "Host": f"[{host}]" if ":" in host else host,
                        "User-Agent": "Fariz-AI-Agent/1.0 (read-only)",
                        "Accept": "text/html,text/plain,application/json,application/xml;q=0.9,*/*;q=0.1",
                        "Accept-Encoding": "identity",
                        "Connection": "close",
                    },
                )
                response = connection.getresponse()
                break
            except (OSError, ssl.SSLError, http.client.HTTPException) as error:
                last_error = error
                if connection is not None:
                    connection.close()
                connection = None
        if response is None or connection is None:
            raise WebFetchError("HTTPS_CONNECTION_FAILED") from last_error

        try:
            status = response.status
            if status in {301, 302, 303, 307, 308}:
                location = response.getheader("Location")
                _read_bounded(response, connection, deadline)
                if redirect_count >= MAX_REDIRECTS or not location:
                    raise WebFetchError("REDIRECT_LIMIT_OR_LOCATION_INVALID")
                current = urljoin(normalized, location)
                continue
            if status < 200 or status >= 300:
                raise WebFetchError(f"HTTP_STATUS_{status}")

            content_type = response.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
            if not any(content_type.startswith(prefix) for prefix in _ALLOWED_CONTENT_TYPES):
                raise WebFetchError("CONTENT_TYPE_NOT_TEXT")
            length = response.getheader("Content-Length")
            if length:
                try:
                    if int(length) > MAX_RESPONSE_BYTES:
                        raise WebFetchError("RESPONSE_TOO_LARGE")
                except ValueError as error:
                    raise WebFetchError("CONTENT_LENGTH_INVALID") from error

            body = _read_bounded(response, connection, deadline)
            charset = "utf-8"
            content_type_header = response.getheader("Content-Type", "")
            for parameter in content_type_header.split(";")[1:]:
                key, separator, value = parameter.strip().partition("=")
                if separator and key.lower() == "charset":
                    charset = value.strip().strip('"')[:40]
                    break
            try:
                text = body.decode(charset, errors="replace")
            except LookupError:
                text = body.decode("utf-8", errors="replace")
            return {
                "success": True,
                "status": "success",
                "url": normalized,
                "http_status": status,
                "content_type": content_type,
                "content_is_untrusted": True,
                "bytes_read": len(body),
                "text": text,
            }
        finally:
            connection.close()
    raise WebFetchError("REDIRECT_LIMIT_EXCEEDED")
