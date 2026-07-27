INGESTION_STATUSES = ("pending", "running", "completed", "failed")
RECORD_STATES = ("provisional", "finalized")
REQUEST_REASONS = ("missing", "partial", "stale", "manual")
SOURCES = ("live_signalr", "fastf1_archive", "jolpica")


def sql_values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)
