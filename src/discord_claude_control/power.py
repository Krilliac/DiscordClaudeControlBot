"""
Hold a Windows power request while a session is active so the PC doesn't
drop into Modern Standby mid-tool-call. When the session goes idle the
request is released and S0ix can park the CPU.

The Windows-specific `kernel32` calls live behind a `_Backend` protocol so
the acquire/release state machine can be unit-tested on any OS with a
recording backend. On non-Windows the default backend is a logging stub,
which lets the rest of the project run for development without crashing.

Not thread-safe. The session state machine (step 6) serializes all calls
through a single asyncio task.
"""

from __future__ import annotations

import logging
import sys
from types import TracebackType
from typing import Protocol

log = logging.getLogger(__name__)

DEFAULT_REASON = "discord-claude-control: active session"


class _Backend(Protocol):
    name: str

    def create(self, reason: str) -> object: ...
    def set_request(self, handle: object) -> None: ...
    def clear_request(self, handle: object) -> None: ...
    def close(self, handle: object) -> None: ...


class _StubBackend:
    """No-op backend for non-Windows platforms. Logs every call."""

    name: str = "stub"
    _next_handle: int = 0

    def create(self, reason: str) -> object:
        _StubBackend._next_handle += 1
        handle = _StubBackend._next_handle
        log.info("[stub power backend] create(reason=%r) -> handle=%d", reason, handle)
        return handle

    def set_request(self, handle: object) -> None:
        log.info("[stub power backend] set_request(handle=%r)", handle)

    def clear_request(self, handle: object) -> None:
        log.info("[stub power backend] clear_request(handle=%r)", handle)

    def close(self, handle: object) -> None:
        log.info("[stub power backend] close(handle=%r)", handle)


def _windows_backend() -> _Backend:
    """
    Build the real ctypes backend by importing kernel32 lazily.

    Layout of the calls matches the C signatures:

        HANDLE PowerCreateRequest(REASON_CONTEXT *Context);
        BOOL   PowerSetRequest(HANDLE PowerRequest, POWER_REQUEST_TYPE Type);
        BOOL   PowerClearRequest(HANDLE PowerRequest, POWER_REQUEST_TYPE Type);
        BOOL   CloseHandle(HANDLE hObject);

    We use PowerRequestSystemRequired (= 1) so the system stays awake. A
    simple-string REASON_CONTEXT makes the request show up by name in
    `powercfg /requests`.
    """
    import ctypes
    from ctypes import wintypes

    power_request_context_version = 0
    power_request_context_simple_string = 0x1
    power_request_system_required = 1

    class _ReasonUnion(ctypes.Union):
        _fields_ = [("SimpleReasonString", ctypes.c_wchar_p)]  # noqa: RUF012  ctypes API

    class _ReasonContext(ctypes.Structure):
        _fields_ = [
            ("Version", wintypes.ULONG),
            ("Flags", wintypes.DWORD),
            ("Reason", _ReasonUnion),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]

    fn_create = kernel32.PowerCreateRequest
    fn_create.argtypes = [ctypes.POINTER(_ReasonContext)]
    fn_create.restype = wintypes.HANDLE

    fn_set = kernel32.PowerSetRequest
    fn_set.argtypes = [wintypes.HANDLE, ctypes.c_int]
    fn_set.restype = wintypes.BOOL

    fn_clear = kernel32.PowerClearRequest
    fn_clear.argtypes = [wintypes.HANDLE, ctypes.c_int]
    fn_clear.restype = wintypes.BOOL

    fn_close = kernel32.CloseHandle
    fn_close.argtypes = [wintypes.HANDLE]
    fn_close.restype = wintypes.BOOL

    get_last_error = ctypes.get_last_error  # type: ignore[attr-defined]

    class _WindowsBackend:
        name: str = "windows"

        def create(self, reason: str) -> object:
            ctx = _ReasonContext()
            ctx.Version = power_request_context_version
            ctx.Flags = power_request_context_simple_string
            ctx.Reason.SimpleReasonString = reason
            handle = fn_create(ctypes.byref(ctx))
            if not handle:
                err = get_last_error()
                raise OSError(err, f"PowerCreateRequest failed (winerror {err})")
            return handle

        def set_request(self, handle: object) -> None:
            if not fn_set(handle, power_request_system_required):
                err = get_last_error()
                raise OSError(err, f"PowerSetRequest failed (winerror {err})")

        def clear_request(self, handle: object) -> None:
            if not fn_clear(handle, power_request_system_required):
                err = get_last_error()
                # Best-effort on release: log but do not raise.
                log.warning("PowerClearRequest failed (winerror %d)", err)

        def close(self, handle: object) -> None:
            if not fn_close(handle):
                err = get_last_error()
                log.warning("CloseHandle failed (winerror %d)", err)

    return _WindowsBackend()


def _default_backend() -> _Backend:
    if sys.platform == "win32":
        try:
            return _windows_backend()
        except Exception:  # pragma: no cover - only triggered on Windows w/o kernel32
            log.exception("Windows power backend init failed; falling back to stub")
            return _StubBackend()
    return _StubBackend()


class PowerRequest:
    """
    Wraps a single Windows system-required power request.

    Idempotent on both acquire (no double-handle leak) and release (safe to
    call twice). Usable as a context manager.
    """

    def __init__(
        self,
        reason: str = DEFAULT_REASON,
        backend: _Backend | None = None,
    ) -> None:
        self._reason = reason
        self._backend: _Backend = backend if backend is not None else _default_backend()
        self._handle: object | None = None

    @property
    def held(self) -> bool:
        return self._handle is not None

    @property
    def backend_name(self) -> str:
        return self._backend.name

    @property
    def reason(self) -> str:
        return self._reason

    def acquire(self) -> None:
        if self._handle is not None:
            return
        handle = self._backend.create(self._reason)
        try:
            self._backend.set_request(handle)
        except Exception:
            # set_request failed: clean up the dangling handle before re-raising
            try:
                self._backend.close(handle)
            except Exception:
                log.exception("close after failed set_request raised; swallowing")
            raise
        self._handle = handle
        log.info("acquired power request: %s", self._reason)

    def release(self) -> None:
        if self._handle is None:
            return
        handle = self._handle
        self._handle = None
        try:
            self._backend.clear_request(handle)
        finally:
            self._backend.close(handle)
        log.info("released power request: %s", self._reason)

    def __enter__(self) -> PowerRequest:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()
