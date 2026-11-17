"""The single exception type the backend raises to its callers."""


class ServiceError(Exception):
    """A user-facing error. The message is safe to show in the UI."""
