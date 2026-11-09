import pytest


def test_default_list_preserves_order_and_total(service):
    result = service.list_tickets()
    assert [item["id"] for item in result["items"]] == [6, 4, 5, 2, 3, 1]
    assert result["total"] == 6


def test_default_pagination_is_unchanged(service):
    result = service.list_tickets(page=2, page_size=2)
    assert [item["id"] for item in result["items"]] == [5, 2]
    assert result["page"] == 2
    assert result["page_size"] == 2
    assert result["total"] == 6


def test_response_schema_is_unchanged(service):
    result = service.list_tickets()
    assert set(result) == {"items", "page", "page_size", "total"}
    assert set(result["items"][0]) == {"id", "title", "status", "created_at"}


def test_explicit_none_status_matches_default(service):
    assert service.list_tickets(status=None) == service.list_tickets()


@pytest.mark.parametrize("page,page_size", [(0, 20), (1, 0), (-1, 20), (1, -1)])
def test_invalid_pagination_is_still_rejected(service, page, page_size):
    with pytest.raises(ValueError):
        service.list_tickets(page=page, page_size=page_size)
