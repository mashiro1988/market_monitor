from __future__ import annotations

import math

MAX_PAGE_SIZE = 500
DEFAULT_PAGE_SIZE = 50


def clamp_page(page: int = 1, page_size: int = DEFAULT_PAGE_SIZE) -> tuple[int, int]:
    page = max(1, int(page or 1))
    page_size = max(1, min(MAX_PAGE_SIZE, int(page_size or DEFAULT_PAGE_SIZE)))
    return page, page_size


def page_count(total: int, page_size: int) -> int:
    if total <= 0:
        return 0
    return math.ceil(total / page_size)
