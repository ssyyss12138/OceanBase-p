import pytest


def test_open_filter_limits_items_and_total(service):
    result = service.list_tickets(status="open")
    assert [item["id"] for item in result["items"]] == [6, 4, 1]
    assert result["total"] == 3


def test_filter_applies_before_pagination(service):
    result = service.list_tickets(status="open", page=3, page_size=1)
    assert [item["id"] for item in result["items"]] == [1]
    assert result["page"] == 3
    assert result["page_size"] == 1
    assert result["total"] == 3


def test_closed_filter_returns_only_closed_tickets(service):
    result = service.list_tickets(status="closed")
    assert [item["id"] for item in result["items"]] == [2]
    assert result["total"] == 1


def test_invalid_status_is_rejected(service):
    with pytest.raises(ValueError, match="unsupported status"):
        service.list_tickets(status="deleted")
