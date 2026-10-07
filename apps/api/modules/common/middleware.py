"""Request correlation (SVX-TECH-001 section 12.1)."""

from __future__ import annotations

import uuid
from collections.abc import Callable

from django.http import HttpRequest, HttpResponse

from modules.common.logging import bind_request_context, clear_request_context

REQUEST_ID_HEADER = "X-Request-ID"


class RequestIdMiddleware:
    """Attach a request id to the request, the logs and the response.

    An inbound header is accepted for tracing across the reverse proxy but is
    bounded and sanitised: it reaches log files, so an unbounded client-supplied
    string would be a log-injection vector.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        request.request_id = self._resolve(request)
        bind_request_context(request_id=request.request_id)
        try:
            response = self.get_response(request)
        finally:
            clear_request_context()
        response[REQUEST_ID_HEADER] = request.request_id
        return response

    @staticmethod
    def _resolve(request: HttpRequest) -> str:
        supplied = request.headers.get(REQUEST_ID_HEADER, "")
        candidate = "".join(c for c in supplied if c.isalnum() or c in "-_")[:64]
        return candidate or uuid.uuid4().hex
