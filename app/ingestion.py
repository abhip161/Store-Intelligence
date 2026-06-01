from __future__ import annotations

import uuid

from pydantic import ValidationError

from app.models import EventIn, IngestRequest, IngestResponse, RejectedEvent
from app.repository import EventRepository


class IngestionService:
    def __init__(self, repository: EventRepository) -> None:
        self.repository = repository

    def ingest(self, request: IngestRequest) -> IngestResponse:
        accepted = 0
        duplicates = 0
        errors: list[RejectedEvent] = []

        for index, raw_event in enumerate(request.events):
            event_id = raw_event.get("event_id") if isinstance(raw_event, dict) else None
            try:
                event = EventIn.model_validate(raw_event)
            except ValidationError as exc:
                errors.append(
                    RejectedEvent(
                        index=index,
                        event_id=str(event_id) if event_id is not None else None,
                        code="VALIDATION_ERROR",
                        message=exc.errors()[0]["msg"] if exc.errors() else "Invalid event",
                    )
                )
                continue

            inserted = self.repository.insert_event(event)
            if inserted:
                accepted += 1
            else:
                duplicates += 1

        batch_id = self.repository.record_batch(accepted, duplicates, len(errors))
        return IngestResponse(
            batch_id=batch_id if isinstance(batch_id, uuid.UUID) else uuid.UUID(str(batch_id)),
            accepted=accepted,
            duplicates=duplicates,
            rejected=len(errors),
            errors=errors,
        )

