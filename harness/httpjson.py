"""Small JSON-over-HTTP helper built on urllib, so the harness needs no pip install."""

import base64
import json
import time
import urllib.error
import urllib.request


class HttpError(Exception):
    def __init__(self, method, url, status, body):
        super().__init__("%s %s -> %s: %s" % (method, url, status, body))
        self.method = method
        self.url = url
        self.status = status
        self.body = body


def request(
    method,
    url,
    *,
    payload=None,
    data=None,
    content_type=None,
    auth=None,
    timeout=120,
    accept_status=None,
):
    """Perform one request.

    `payload` is serialised as JSON; `data` is sent as raw bytes. Returns
    (status, parsed_body): parsed as JSON when the response says JSON, bytes otherwise.
    Raises HttpError for statuses outside `accept_status` (2xx by default).
    """
    headers = {"Accept": "application/json"}
    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = content_type or "application/json"
    elif data is not None:
        body = data if isinstance(data, bytes) else data.encode("utf-8")
        headers["Content-Type"] = content_type or "application/octet-stream"
    if auth:
        token = base64.b64encode(("%s:%s" % auth).encode("utf-8")).decode("ascii")
        headers["Authorization"] = "Basic " + token

    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status, _parse(response.read(), response.headers)
    except urllib.error.HTTPError as error:
        raw = error.read()
        parsed = _parse(raw, error.headers)
        if accept_status and error.code in accept_status:
            return error.code, parsed
        raise HttpError(method, url, error.code, parsed) from None


def _parse(raw, headers):
    if not raw:
        return None
    if "json" in (headers.get("Content-Type") or ""):
        return json.loads(raw.decode("utf-8"))
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return raw


def wait_until(predicate, *, timeout, interval=3.0, description="condition"):
    """Poll `predicate` until it returns a truthy value. Returns that value.

    Raises TimeoutError with the description, so a failing wait says what it was waiting for.
    """
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            value = predicate()
            if value:
                return value
            last_error = None
        except (HttpError, urllib.error.URLError, OSError) as error:
            last_error = error
        time.sleep(interval)
    suffix = " (last error: %s)" % last_error if last_error else ""
    raise TimeoutError("timed out after %ss waiting for %s%s" % (timeout, description, suffix))
