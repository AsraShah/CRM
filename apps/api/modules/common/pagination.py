"""Cursor pagination (SVX-TECH-001 section 6.1).

Default 50, maximum 100. Cursor rather than offset pagination so that a page
boundary stays stable while records are being created underneath it -- offset
paging silently skips or repeats rows on a moving queue.
"""

from __future__ import annotations

from rest_framework.pagination import CursorPagination


class WorkspaceCursorPagination(CursorPagination):
    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 100
    # Descending creation order is stable for append-heavy tables. Views with a
    # different natural order (due date, stage age) override `ordering` and must
    # order on a field with a supporting index.
    ordering = "-created_at"
