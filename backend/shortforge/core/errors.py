"""Shared exception types."""

from __future__ import annotations


class ShortForgeError(Exception):
    """Base error with a user-readable message."""

    retryable: bool = False

    def __init__(self, message: str, *, detail: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail


class RetryableError(ShortForgeError):
    retryable = True


class JobCancelled(ShortForgeError):
    def __init__(self) -> None:
        super().__init__("Job was cancelled")


class EntityGone(JobCancelled):
    """The record a job works on was deleted meanwhile: stop quietly with a readable reason."""

    def __init__(self, what: str) -> None:
        ShortForgeError.__init__(self, f"Stopped: the {what} was deleted.")


class DependencyMissing(ShortForgeError):
    """A required tool/model is not installed (e.g. FFmpeg, a Whisper model)."""


class SourceResolutionError(ShortForgeError):
    pass


class DownloadError(RetryableError):
    pass


class GPUMemoryError(RetryableError):
    pass


class FFmpegError(ShortForgeError):
    def __init__(self, message: str, *, stderr: str = "", cmd: list[str] | None = None) -> None:
        tail = "\n".join(stderr.strip().splitlines()[-15:])
        super().__init__(message, detail=tail)
        self.stderr = stderr
        self.cmd = cmd or []


def is_cuda_oom(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(
        needle in text
        for needle in ("out of memory", "cuda_error_out_of_memory", "cublas_status_alloc_failed", "cudnn_status_alloc")
    )
