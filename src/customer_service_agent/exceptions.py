class CustomerServiceError(Exception):
    """Base exception for controlled service failures."""


class BackendError(CustomerServiceError):
    def __init__(
        self,
        message: str,
        *,
        error_code: str = "BUSINESS_API_ERROR",
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.retryable = retryable


class BackendTimeoutError(BackendError):
    def __init__(self, message: str = "business API timed out") -> None:
        super().__init__(
            message,
            error_code="BUSINESS_API_TIMEOUT",
            retryable=True,
        )


class ConversationStateError(CustomerServiceError):
    """Raised when stored scene state is internally inconsistent."""


class ModelUnavailableError(CustomerServiceError):
    """Raised when optional model understanding is unavailable."""
