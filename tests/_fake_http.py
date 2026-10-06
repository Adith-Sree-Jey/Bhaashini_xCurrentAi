"""A minimal requests.Session-compatible fake, for hermetic client tests.

No test may start the mock server or touch the network (CLAUDE.md): this
replaces the HTTP transport BhashiniClient/OllamaClient use entirely,
either by replaying recorded fixture files (the same JSON shape
BhashiniClient's `record_dir` writes -- see
tests/fixtures/contract/README.md) or from an explicit in-test response
queue, for exercising error/retry paths a happy-path recorded corpus
doesn't cover.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Union

import requests

#: Either a (status_code, json_body) pair to return, or an exception
#: instance to raise -- lets tests simulate a connection failure.
QueueItem = Union[tuple, BaseException]


class FakeResponse:
    def __init__(self, status_code: int, json_body=None, text: str = None):
        self.status_code = status_code
        self._json_body = json_body
        if text is not None:
            self.text = text
        elif json_body is not None:
            self.text = json.dumps(json_body)
        else:
            self.text = ""

    def json(self):
        if self._json_body is None:
            raise ValueError("FakeResponse has no JSON body")
        return self._json_body


class FakeSession:
    """Serves canned responses for GET/POST calls, keyed by endpoint (the
    URL's last path segment, e.g. "asr", "health", "generate"), consumed in
    order per endpoint. Once a queue is exhausted, repeats the last item
    rather than raising, so a test that calls a method more times than it
    queued responses for still gets something deterministic.
    """

    def __init__(self, responses: Dict[str, List[QueueItem]]):
        self._queues = {k: list(v) for k, v in responses.items()}
        self._cursors = {k: 0 for k in responses}
        #: Every call made, in order, for tests that want to assert on
        #: exactly what was sent (e.g. language-code casing at the wire).
        self.requests: List[dict] = []

    @classmethod
    def from_fixture_dir(cls, fixtures_dir: Union[str, Path]) -> "FakeSession":
        """Load every recorded `<endpoint>/<timestamp>.json` file under
        `fixtures_dir` (the shape BhashiniClient's `record_dir` writes),
        sorted by filename (i.e. recording order), as that endpoint's
        response queue."""
        fixtures_dir = Path(fixtures_dir)
        responses: Dict[str, List[QueueItem]] = {}
        for endpoint_dir in sorted(p for p in fixtures_dir.iterdir() if p.is_dir()):
            queue: List[QueueItem] = []
            for record_file in sorted(endpoint_dir.glob("*.json")):
                record = json.loads(record_file.read_text())
                queue.append((record["status_code"], record["response"]))
            if queue:
                responses[endpoint_dir.name] = queue
        return cls(responses)

    @staticmethod
    def _endpoint_of(url: str) -> str:
        return url.rstrip("/").rsplit("/", 1)[-1]

    def _next(self, endpoint: str) -> QueueItem:
        queue = self._queues.get(endpoint)
        if not queue:
            raise AssertionError(f"FakeSession has no queued response for endpoint {endpoint!r}")
        idx = min(self._cursors[endpoint], len(queue) - 1)
        self._cursors[endpoint] = idx + 1
        return queue[idx]

    def _respond(self, endpoint: str) -> FakeResponse:
        item = self._next(endpoint)
        if isinstance(item, BaseException):
            raise item
        status_code, body = item
        return FakeResponse(status_code, json_body=body)

    def get(self, url, timeout=None):
        self.requests.append({"method": "GET", "url": url, "timeout": timeout})
        return self._respond(self._endpoint_of(url))

    def post(self, url, json=None, timeout=None):
        self.requests.append({"method": "POST", "url": url, "json": json, "timeout": timeout})
        return self._respond(self._endpoint_of(url))


def connection_error(message: str = "connection refused") -> requests.exceptions.ConnectionError:
    """Convenience for queuing a simulated transport failure, e.g.
    `FakeSession({"asr": [connection_error(), (200, {...})]})` to test that
    BhashiniClient retries past one dropped connection."""
    return requests.exceptions.ConnectionError(message)
