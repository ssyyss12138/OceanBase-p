import pytest

from ticket_service import Ticket, TicketService


@pytest.fixture
def service():
    return TicketService(
        [
            Ticket(1, "first", "open", "2026-01-01T09:00:00Z"),
            Ticket(2, "second", "closed", "2026-01-03T09:00:00Z"),
            Ticket(3, "third", "in_progress", "2026-01-02T09:00:00Z"),
            Ticket(4, "fourth", "open", "2026-01-05T09:00:00Z"),
            Ticket(5, "fifth", "resolved", "2026-01-04T09:00:00Z"),
            Ticket(6, "sixth", "open", "2026-01-05T09:00:00Z"),
        ]
    )
