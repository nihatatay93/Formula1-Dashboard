from datetime import datetime


class FastF1RequestBudgetExhaustedError(RuntimeError):
    """Raised before an outbound request when local safety capacity is full."""

    def __init__(self, *, retry_at: datetime) -> None:
        super().__init__("FastF1 local request safety budget is exhausted")
        self.retry_at = retry_at
