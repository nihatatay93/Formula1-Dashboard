"""Sporting rules shared by every season-scoped aggregate.

These predicates decide what counts, and more than one endpoint has to agree on
them: standings, head to head and consistency all answer questions about the
same season, and a driver who is a DNF in one must be a DNF in the others.
"""

from __future__ import annotations

from sqlalchemy import func

from app.db.models import RaceSession, SessionIngestion, SessionResult

#: A session counts once its ingestion has completed. This is the same rule the
#: season overview uses for ``data_available``; the two must not drift.
COMPLETED = SessionIngestion.completed_at.is_not(None)

#: Only a numeric classified position means the driver was classified. A
#: retirement past ninety per cent of the race distance still is, which is why
#: this is read from the data rather than inferred from the status text -- of 46
#: rows reading "Retired" in the archive, six carry a classified position.
#:
#: Wrapped in coalesce because the column is NULL for the clearest non-finishes,
#: and in SQL "NOT NULL" is NULL rather than true: without this a driver who did
#: not finish at all was counted as neither classified nor retired, and vanished
#: from the DNF tally entirely.
CLASSIFIED = func.coalesce(
    SessionResult.classified_position.op("~")("^[0-9]+$"), False
)

IS_RACE = RaceSession.session_key == "race"
IS_QUALIFYING = RaceSession.session_key == "qualifying"
