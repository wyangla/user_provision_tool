"""Unit tests for the async task pool (lib/task_manager.py)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


class TestTaskManager:
    """Verify the async task pool: submit → status → complete → cancel."""

    def test_submit_and_complete(self):
        from lib.task_manager import TaskManager
        tm = TaskManager(max_workers=2)

        def slow_add(a, b):
            import time
            time.sleep(0.05)
            return a + b

        task_id = tm.submit("test", slow_add, 1, 2)
        assert len(task_id) == 12  # short UUID

        # Poll until completed
        import time
        for _ in range(50):
            task = tm.get(task_id)
            if task["status"] in ("completed", "failed"):
                break
            time.sleep(0.01)

        assert task["status"] == "completed"
        assert task["result"] == 3
        assert task["error"] is None
        assert task["type"] == "test"

    def test_submit_and_fail(self):
        from lib.task_manager import TaskManager
        tm = TaskManager(max_workers=2)

        def raiser():
            raise ValueError("boom")

        task_id = tm.submit("test", raiser)
        import time
        for _ in range(50):
            task = tm.get(task_id)
            if task["status"] in ("completed", "failed"):
                break
            time.sleep(0.01)

        assert task["status"] == "failed"
        assert "boom" in (task["error"] or "")
        assert task["result"] is None

    def test_cancel_pending_task(self):
        from lib.task_manager import TaskManager
        tm = TaskManager(max_workers=1)

        def blocker():
            import time
            time.sleep(10)

        # Use the only worker so the second task stays pending
        tm.submit("block", blocker)
        task_id = tm.submit("test", lambda: 42)

        assert tm.cancel(task_id) is True
        task = tm.get(task_id)
        assert task["status"] == "cancelled"

    def test_get_nonexistent_returns_none(self):
        from lib.task_manager import TaskManager
        tm = TaskManager()
        assert tm.get("nonexistent") is None

    def test_cancel_nonexistent_returns_false(self):
        from lib.task_manager import TaskManager
        tm = TaskManager()
        assert tm.cancel("nonexistent") is False

    def test_cancel_completed_returns_false(self):
        from lib.task_manager import TaskManager
        tm = TaskManager(max_workers=2)
        task_id = tm.submit("test", lambda: None)
        import time
        for _ in range(50):
            if tm.get(task_id)["status"] == "completed":
                break
            time.sleep(0.01)
        assert tm.cancel(task_id) is False  # already completed

    def test_task_id_uniqueness(self):
        from lib.task_manager import TaskManager
        tm = TaskManager(max_workers=4)
        ids = {tm.submit("test", lambda: None) for _ in range(20)}
        assert len(ids) == 20

    def test_task_dict_structure(self):
        from lib.task_manager import TaskManager
        tm = TaskManager(max_workers=2)

        def echo(x):
            return x

        tid = tm.submit("register", echo, 42)
        import time
        task = None
        for _ in range(50):
            task = tm.get(tid)
            if task["status"] in ("completed", "failed"):
                break
            time.sleep(0.01)

        assert set(task.keys()) == {
            "task_id", "type", "status", "created_at", "updated_at", "result", "error"
        }
        assert task["task_id"] == tid
        assert task["type"] == "register"
        assert task["status"] == "completed"
        assert task["result"] == 42

    def test_list_all_returns_all_tasks(self):
        from lib.task_manager import TaskManager
        tm = TaskManager(max_workers=4)
        import time

        ids = [tm.submit("test", lambda x: x, i) for i in range(3)]
        for _ in range(50):
            tasks = tm.list_all()
            if all(t["status"] in ("completed", "failed") for t in tasks):
                break
            time.sleep(0.01)

        assert len(tasks) == 3
        # Newest first
        assert tasks[0]["task_id"] == ids[2]
        assert tasks[2]["task_id"] == ids[0]

    def test_list_all_empty(self):
        from lib.task_manager import TaskManager
        tm = TaskManager()
        tasks = tm.list_all()
        assert tasks == []
