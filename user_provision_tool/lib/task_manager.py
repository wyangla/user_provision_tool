"""lib/task_manager.py — lightweight async task pool for provision-api.

Provides a thread-pool-backed task queue so long-running Docker operations
(register, rebuild, remove) don't block the API.  Each task gets a UUID,
runs on a worker thread, and reports status through in-memory storage.

Usage::

    from lib.task_manager import task_manager

    task_id = task_manager.submit("register", provisioner.register_user, **kwargs)
    # → returns immediately with task_id

    status = task_manager.get(task_id)
    # → {"task_id": "...", "type": "register", "status": "running", ...}

    task_manager.cancel(task_id)
    # → marks as cancelled; the thread will stop at the next docker_ops call
"""

from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable


class Task:
    """A single task tracked by the TaskManager."""

    __slots__ = (
        "task_id", "type", "status", "created_at", "updated_at",
        "result", "error", "_future", "_cancel_event",
    )

    def __init__(self, task_id: str, task_type: str, future: Future):
        self.task_id = task_id
        self.type = task_type          # "register" | "rebuild" | "remove"
        self.status = "pending"        # pending → running → completed | failed | cancelled
        self.created_at = time.time()
        self.updated_at = self.created_at
        self.result: Any = None
        self.error: str | None = None
        self._future = future
        self._cancel_event = threading.Event()

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "type": self.type,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "result": self.result,
            "error": self.error,
        }


class TaskManager:
    """In-memory task pool backed by a ThreadPoolExecutor.

    Tasks are stored in a dict and automatically cleaned up after
    *max_age_seconds* (default: 1 hour).
    """

    def __init__(self, max_workers: int = 4, max_age_seconds: float = 3600):
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._tasks: dict[str, Task] = {}
        self._lock = threading.Lock()
        self._max_age = max_age_seconds

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def submit(
        self,
        task_type: str,
        fn: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> str:
        """Submit *fn* for background execution.  Returns a task UUID immediately."""
        task_id = uuid.uuid4().hex[:12]  # short enough for URLs, unique enough
        # Create a placeholder future — the real submit happens after the task
        # is registered so the worker thread sees it.
        future = Future()
        task = Task(task_id, task_type, future)
        with self._lock:
            self._tasks[task_id] = task

        # Now submit — race is eliminated because the task is already in _tasks
        real_future = self._executor.submit(
            self._run_task, task_id, task_type, fn, *args, **kwargs
        )
        task._future = real_future
        return task_id

    def get(self, task_id: str) -> dict[str, Any] | None:
        """Return the task status dict, or None if not found / cleaned up."""
        self._cleanup_stale()
        with self._lock:
            task = self._tasks.get(task_id)
        if task is None:
            return None
        return task.to_dict()

    def cancel(self, task_id: str) -> bool:
        """Request cancellation of a pending or running task.

        Returns True if the task was found and cancellation was requested.
        The task thread will stop at the next docker_ops call (which checks
        the cancel event between subprocess invocations).
        """
        with self._lock:
            task = self._tasks.get(task_id)
        if task is None:
            return False
        if task.status in ("completed", "failed", "cancelled"):
            return False
        task._cancel_event.set()
        task.status = "cancelled"
        task.updated_at = time.time()
        return True

    def list_all(self) -> list[dict[str, Any]]:
        """Return status dicts for all tasks in the pool, newest first."""
        self._cleanup_stale()
        with self._lock:
            tasks = sorted(
                self._tasks.values(),
                key=lambda t: t.created_at,
                reverse=True,
            )
        return [t.to_dict() for t in tasks]

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _run_task(self, task_id: str, task_type: str, fn: Callable, *args: Any, **kwargs: Any) -> None:
        """Wrapper executed on a worker thread."""
        with self._lock:
            task = self._tasks.get(task_id)
        if task is None:
            return

        task.status = "running"
        task.updated_at = time.time()

        # Strip internal kwargs not meant for the target function
        cancel_event = kwargs.pop("_cancel_event", None)
        if cancel_event is not None:
            task._cancel_event = cancel_event

        try:
            result = fn(*args, **kwargs)
            task.result = result
            task.status = "completed"
        except Exception as e:
            task.error = str(e)
            task.status = "failed"
        finally:
            task.updated_at = time.time()

    def _cleanup_stale(self) -> None:
        """Remove tasks older than _max_age that have finished."""
        now = time.time()
        with self._lock:
            stale = [
                tid for tid, t in self._tasks.items()
                if t.status in ("completed", "failed", "cancelled")
                and (now - t.updated_at) > self._max_age
            ]
            for tid in stale:
                del self._tasks[tid]


# ---------------------------------------------------------------------------
# Singleton instance
# ---------------------------------------------------------------------------

task_manager = TaskManager()
