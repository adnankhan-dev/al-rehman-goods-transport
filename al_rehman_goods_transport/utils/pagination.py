from math import ceil

DEFAULT_PER_PAGE = 50


def parse_page(value):
    try:
        page = int(value)
    except (TypeError, ValueError):
        return 1
    return max(1, page)


def paginate_list(items, page, per_page=DEFAULT_PER_PAGE):
    total = len(items)
    total_pages = max(1, ceil(total / per_page)) if per_page else 1
    page = min(max(1, page), total_pages)
    start = (page - 1) * per_page
    page_items = items[start:start + per_page]
    return {
        "items": page_items,
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": total_pages,
        "has_prev": page > 1,
        "has_next": page < total_pages,
        "start_index": start + 1 if page_items else 0,
        "end_index": start + len(page_items),
    }


def build_pagination(page_items, page, per_page, total):
    """Pagination context for results already limited at the query level."""
    total_pages = max(1, ceil(total / per_page)) if per_page else 1
    page = min(max(1, page), total_pages)
    start = (page - 1) * per_page
    return {
        "items": page_items,
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": total_pages,
        "has_prev": page > 1,
        "has_next": page < total_pages,
        "start_index": start + 1 if page_items else 0,
        "end_index": start + len(page_items),
    }
