from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Ticket:
    id: int
    title: str
    status: str
    created_at: str


class TicketService:
    VALID_STATUSES = ("open", "in_progress", "resolved", "closed")

    def __init__(self, tickets):
        self._tickets = tuple(tickets)

    def list_tickets(self, page=1, page_size=20, status=None):
        if page < 1:
            raise ValueError("page must be at least 1")
        if page_size < 1:
            raise ValueError("page_size must be at least 1")
        if status is not None and status not in self.VALID_STATUSES:
            raise ValueError(f"unsupported status: {status}")

        filtered = (
            self._tickets
            if status is None
            else tuple(ticket for ticket in self._tickets if ticket.status == status)
        )
        ordered = sorted(filtered, key=lambda ticket: (ticket.created_at, ticket.id), reverse=True)
        start = (page - 1) * page_size
        items = ordered[start : start + page_size]
        return {
            "items": [asdict(ticket) for ticket in items],
            "page": page,
            "page_size": page_size,
            "total": len(ordered),
        }
