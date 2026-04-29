"""
exceptions.py — Custom exception hierarchy for the subtitle translation pipeline.

Provides specific exception types for API errors, content blocking,
line count mismatches, subtitle parsing failures, and MKV operations.
"""
class SubtitleTranslationError(Exception):
    """Base exception for subtitle translation project."""
    pass

class APIConnectionError(SubtitleTranslationError):
    """Error connecting to Gemini API."""
    pass

class APIResponseError(SubtitleTranslationError):
    """Invalid or unexpected response from API."""
    pass

class LineCountMismatchError(APIResponseError):
    """Number of translated lines does not match expected count."""
    def __init__(self, expected: int, received: int, missing_indices: list[int] | None = None):
        self.expected = expected
        self.received = received
        self.missing_indices = missing_indices or []
        super().__init__(f"Expected {expected} lines, but received {received}. Missing: {self.missing_indices}")

class ContentBlockedError(APIResponseError):
    """Content blocked by safety filters."""
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(f"Content blocked: {reason}")

class SubtitleParsingError(SubtitleTranslationError):
    """Error parsing subtitle files."""
    pass

class MKVOperationError(SubtitleTranslationError):
    """Error during MKV operations (extract/merge)."""
    pass

class TranslationTimeoutError(SubtitleTranslationError):
    """API call timed out."""
    pass
