import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.ingestion.archive_attempt import sanitize_archive_failure
from app.ingestion.archive_ingestion import ArchiveSessionIdentityError
from app.ingestion.archive_persistence import (
    ArchivePersistenceContractError,
    ArchivePersistenceTargetChangedError,
    ArchiveSessionNotFoundError,
    ArchiveSourceConflictError,
)
from app.ingestion.fastf1_loader import (
    FastF1LoaderConfigurationError,
    FastF1RateLimitError,
    FastF1SessionLoadError,
)
from app.ingestion.fastf1_normalization import FastF1NormalizationError


@pytest.mark.parametrize(
    ("error", "expected_code", "expected_message"),
    [
        (
            FastF1RateLimitError("RAW-ERROR-SENTINEL"),
            "fastf1_rate_limited",
            "FastF1 archive request rate limit was reached.",
        ),
        (
            FastF1LoaderConfigurationError("RAW-ERROR-SENTINEL"),
            "fastf1_configuration_failed",
            "FastF1 loader configuration failed.",
        ),
        (
            FastF1SessionLoadError("RAW-ERROR-SENTINEL"),
            "fastf1_load_failed",
            "FastF1 session loading failed.",
        ),
        (
            FastF1NormalizationError("RAW-ERROR-SENTINEL"),
            "fastf1_normalization_failed",
            "FastF1 session normalization failed.",
        ),
        (
            ArchiveSessionIdentityError("RAW-ERROR-SENTINEL"),
            "archive_identity_mismatch",
            "Loaded archive identity did not match the database session.",
        ),
        (
            ArchivePersistenceTargetChangedError("RAW-ERROR-SENTINEL"),
            "archive_target_changed",
            "Archive target identity changed before persistence.",
        ),
        (
            ArchiveSourceConflictError("RAW-ERROR-SENTINEL"),
            "archive_source_conflict",
            "Archive replacement conflicted with another data source.",
        ),
        (
            ArchiveSessionNotFoundError("RAW-ERROR-SENTINEL"),
            "archive_target_missing",
            "The archive target session no longer exists.",
        ),
        (
            ArchivePersistenceContractError("RAW-ERROR-SENTINEL"),
            "archive_persistence_failed",
            "Archive snapshot persistence failed.",
        ),
        (
            SQLAlchemyError("RAW-ERROR-SENTINEL"),
            "database_operation_failed",
            "A database operation failed during archive ingestion.",
        ),
        (
            RuntimeError("RAW-ERROR-SENTINEL"),
            "archive_ingestion_failed",
            "Archive session ingestion failed.",
        ),
    ],
)
def test_failure_sanitization_uses_fixed_secret_free_diagnostics(
    error: Exception,
    expected_code: str,
    expected_message: str,
) -> None:
    failure = sanitize_archive_failure(error)

    assert failure.code == expected_code
    assert failure.message == expected_message
    assert "RAW-ERROR-SENTINEL" not in failure.message
