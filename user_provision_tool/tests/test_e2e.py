"""End-to-end tests for the user provision tool scripts.

These tests exercise the full registration → status → rebuild → removal
workflow by running the actual entry-point scripts through subprocess, with:
  - docker compose calls mocked out (subprocess.run patched)
  - a temp directory used for generated/ and user_registry.yml
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch, call

import pytest
import yaml

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

FIXTURES_DIR = Path(__file__).parent / "fixtures"
COMPOSE_TEMPLATE = str(FIXTURES_DIR / "docker-compose.template.yml.j2")
NGINX_TEMPLATE = str(FIXTURES_DIR / "myapp.template.nginx.conf.j2")

from lib import registry as reg_mod
from lib import docker_ops


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_script(script: str, args: list[str], env_overrides: dict | None = None) -> subprocess.CompletedProcess:
    """Run a provision script in-process via importlib to allow monkeypatching."""
    raise NotImplementedError  # not used — we call main() directly instead


def _make_docker_mock(returncode: int = 0) -> MagicMock:
    """Return a mock that mimics a successful subprocess.CompletedProcess."""
    m = MagicMock()
    m.returncode = returncode
    return m


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Redirect REGISTRY_FILE and generated/ to tmp_path for every e2e test."""
    monkeypatch.setattr(reg_mod, "REGISTRY_FILE", tmp_path / "user_registry.yml")
    # Patch the GENERATED_DIR used in cli.register
    import cli.register as reg_script
    monkeypatch.setattr(reg_script, "GENERATED_DIR", tmp_path / "generated")
    (tmp_path / "generated").mkdir()
    return tmp_path


@pytest.fixture()
def mock_docker(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Patch subprocess.run inside docker_ops to prevent real docker calls."""
    mock = MagicMock(return_value=_make_docker_mock(0))
    monkeypatch.setattr(docker_ops.subprocess, "run", mock)
    return mock


@pytest.fixture()
def mock_docker_ps_empty(monkeypatch: pytest.MonkeyPatch):
    """Make docker_ps return no containers."""
    monkeypatch.setattr(docker_ops, "docker_ps", lambda: [])


@pytest.fixture()
def registered_alice(
    tmp_path: Path,
    mock_docker: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> dict:
    """Run cli.register for alice and return the registry entry."""
    import cli.register as reg_script

    monkeypatch.setattr("getpass.getpass", lambda prompt="": "secret123")
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")

    sys_argv = [
        "cli/register.py",
        "-u", "alice",
        "-sn", "myapp",
        "-tc", COMPOSE_TEMPLATE,
        "-tn", NGINX_TEMPLATE,
        "-l", "0",
        "-d", "example.com",
        "-v", "app_data=/srv/alice/app",
        "-v", "db_data=/srv/alice/db",
    ]
    with patch.object(sys, "argv", sys_argv):
        reg_script.main()

    entry = reg_mod.get_user_service("alice", "myapp", "0")
    assert entry is not None, "Registration did not write registry entry"
    return entry


# ---------------------------------------------------------------------------
# E2E: Registration
# ---------------------------------------------------------------------------

class TestE2ERegistration:
    def test_registry_entry_created(self, registered_alice):
        assert registered_alice["user_name"] == "alice"
        assert registered_alice["service_name"] == "myapp"
        assert registered_alice["label"] == "0"
        assert registered_alice["volumes"]["app_data"] == "/srv/alice/app"
        # network_name must be stored in the registry entry
        assert registered_alice["network_name"] == "myapp-user_alice-0"

    def test_compose_file_generated(self, tmp_path, registered_alice):
        compose_path = Path(registered_alice["compose_file_path"])
        assert compose_path.exists()
        data = yaml.safe_load(compose_path.read_text())
        assert "web" in data["services"]
        assert "db" in data["services"]
        assert data["services"]["web"]["container_name"] == "myapp-user_alice-0-web"
        # Isolated network must be declared at top level and referenced by services
        expected_net = "myapp-user_alice-0"
        assert expected_net in data.get("networks", {})
        assert expected_net in data["services"]["web"].get("networks", [])
        assert expected_net in data["services"]["db"].get("networks", [])

    def test_nginx_conf_generated(self, registered_alice):
        nginx_path = Path(registered_alice["nginx_conf_path"])
        assert nginx_path.exists()
        content = nginx_path.read_text()
        assert "server_name myapp-alice-0.example.com;" in content
        assert "auth_basic_user_file" in content

    def test_htpasswd_file_generated(self, registered_alice):
        htpasswd_path = Path(registered_alice["htpasswd_path"])
        assert htpasswd_path.exists()
        content = htpasswd_path.read_text()
        assert content.startswith("alice:$2")

    def test_docker_compose_up_called(self, registered_alice, mock_docker):
        up_calls = [
            c for c in mock_docker.call_args_list
            if "up" in (c.args[0] if c.args else [])
        ]
        assert len(up_calls) >= 1

    def test_duplicate_registration_fails(
        self, registered_alice, mock_docker, monkeypatch
    ):
        import cli.register as reg_script
        monkeypatch.setattr("getpass.getpass", lambda prompt="": "secret123")
        sys_argv = [
            "cli/register.py",
            "-u", "alice", "-sn", "myapp",
            "-tc", COMPOSE_TEMPLATE, "-l", "0",
            "-v", "app_data=/srv/alice/app",
            "-v", "db_data=/srv/alice/db",
        ]
        with patch.object(sys, "argv", sys_argv):
            with pytest.raises(SystemExit) as exc:
                reg_script.main()
        assert exc.value.code != 0

    def test_invalid_username_rejected(self, mock_docker, monkeypatch):
        import cli.register as reg_script
        monkeypatch.setattr("getpass.getpass", lambda prompt="": "")
        sys_argv = [
            "cli/register.py",
            "-u", "alice!", "-sn", "myapp",
            "-tc", COMPOSE_TEMPLATE, "-l", "0",
        ]
        with patch.object(sys, "argv", sys_argv):
            with pytest.raises(SystemExit) as exc:
                reg_script.main()
        assert exc.value.code != 0

    def test_invalid_label_rejected(self, mock_docker, monkeypatch):
        import cli.register as reg_script
        monkeypatch.setattr("getpass.getpass", lambda prompt="": "")
        sys_argv = [
            "cli/register.py",
            "-u", "alice", "-sn", "myapp",
            "-tc", COMPOSE_TEMPLATE, "-l", "abc",
        ]
        with patch.object(sys, "argv", sys_argv):
            with pytest.raises(SystemExit) as exc:
                reg_script.main()
        assert exc.value.code != 0

    def test_volume_mismatch_abort(self, mock_docker, monkeypatch):
        """When volumes don't match and user types 'n', registration aborts."""
        import cli.register as reg_script
        monkeypatch.setattr("getpass.getpass", lambda prompt="": "")
        monkeypatch.setattr("builtins.input", lambda prompt="": "n")
        sys_argv = [
            "cli/register.py",
            "-u", "carol", "-sn", "myapp",
            "-tc", COMPOSE_TEMPLATE, "-l", "0",
            # intentionally omit volumes to trigger the mismatch warning
        ]
        with patch.object(sys, "argv", sys_argv):
            with pytest.raises(SystemExit) as exc:
                reg_script.main()
        assert exc.value.code == 0  # exits cleanly with "Aborted"
        assert reg_mod.get_user_service("carol", "myapp", "0") is None

    def test_volume_mismatch_confirm_continues(self, mock_docker, monkeypatch):
        """When volumes don't match and user types 'y', registration proceeds.
        We supply app_data/db_data (not in template detection list → triggers
        'extra' warning) so the compose template can still render."""
        import cli.register as reg_script
        monkeypatch.setattr("getpass.getpass", lambda prompt="": "")
        monkeypatch.setattr("builtins.input", lambda prompt="": "y")
        sys_argv = [
            "cli/register.py",
            "-u", "dave", "-sn", "myapp",
            "-tc", COMPOSE_TEMPLATE, "-l", "0",
            "-v", "app_data=/data/dave/app",
            "-v", "db_data=/data/dave/db",
        ]
        with patch.object(sys, "argv", sys_argv):
            reg_script.main()
        assert reg_mod.get_user_service("dave", "myapp", "0") is not None

    def test_two_users_have_isolated_networks(self, tmp_path, mock_docker, monkeypatch):
        """Different users must get different network names in the registry."""
        import cli.register as reg_script
        for user, label in [("alice", "0"), ("bob", "1")]:
            monkeypatch.setattr("getpass.getpass", lambda prompt="": "")
            monkeypatch.setattr("builtins.input", lambda prompt="": "y")
            sys_argv = [
                "cli/register.py",
                "-u", user, "-sn", "myapp",
                "-tc", COMPOSE_TEMPLATE,
                "-l", label,
                "-v", f"app_data=/data/{user}/app",
                "-v", f"db_data=/data/{user}/db",
            ]
            with patch.object(sys, "argv", sys_argv):
                reg_script.main()
        e_alice = reg_mod.get_user_service("alice", "myapp", "0")
        e_bob = reg_mod.get_user_service("bob", "myapp", "1")
        assert e_alice["network_name"] != e_bob["network_name"]


# ---------------------------------------------------------------------------
# E2E: Removal
# ---------------------------------------------------------------------------

class TestE2ERemoval:
    def test_removal_deregisters_user(self, registered_alice, mock_docker):
        import cli.remove as rem_script
        sys_argv = [
            "cli/remove.py",
            "-u", "alice", "-sn", "myapp", "-l", "0",
        ]
        with patch.object(sys, "argv", sys_argv):
            rem_script.main()
        assert reg_mod.get_user_service("alice", "myapp", "0") is None

    def test_removal_calls_compose_down(self, registered_alice, mock_docker):
        import cli.remove as rem_script
        sys_argv = [
            "cli/remove.py",
            "-u", "alice", "-sn", "myapp", "-l", "0",
        ]
        with patch.object(sys, "argv", sys_argv):
            rem_script.main()
        down_calls = [
            c for c in mock_docker.call_args_list
            if "down" in (c.args[0] if c.args else [])
        ]
        assert len(down_calls) >= 1

    def test_removal_nonexistent_user_fails(self, mock_docker):
        import cli.remove as rem_script
        sys_argv = [
            "cli/remove.py",
            "-u", "ghost", "-sn", "myapp", "-l", "0",
        ]
        with patch.object(sys, "argv", sys_argv):
            with pytest.raises(SystemExit) as exc:
                rem_script.main()
        assert exc.value.code != 0


# ---------------------------------------------------------------------------
# E2E: Rebuild
# ---------------------------------------------------------------------------

class TestE2ERebuild:
    def test_rebuild_calls_build_and_up(self, registered_alice, mock_docker):
        import cli.rebuild as reb_script
        sys_argv = [
            "cli/rebuild.py",
            "-u", "alice", "-sn", "myapp", "-l", "0",
        ]
        with patch.object(sys, "argv", sys_argv):
            reb_script.main()
        cmds = [c.args[0] for c in mock_docker.call_args_list if c.args]
        assert any("build" in cmd for cmd in cmds)
        assert any("up" in cmd for cmd in cmds)

    def test_rebuild_no_cache_flag_passed(self, registered_alice, mock_docker):
        import cli.rebuild as reb_script
        sys_argv = [
            "cli/rebuild.py",
            "-u", "alice", "-sn", "myapp", "-l", "0", "--no-cache",
        ]
        with patch.object(sys, "argv", sys_argv):
            reb_script.main()
        cmds = [c.args[0] for c in mock_docker.call_args_list if c.args]
        build_cmds = [cmd for cmd in cmds if "build" in cmd]
        assert any("--no-cache" in cmd for cmd in build_cmds)

    def test_rebuild_nonexistent_user_fails(self, mock_docker):
        import cli.rebuild as reb_script
        sys_argv = [
            "cli/rebuild.py",
            "-u", "ghost", "-sn", "myapp", "-l", "0",
        ]
        with patch.object(sys, "argv", sys_argv):
            with pytest.raises(SystemExit) as exc:
                reb_script.main()
        assert exc.value.code != 0


# ---------------------------------------------------------------------------
# E2E: Status
# ---------------------------------------------------------------------------

class TestE2EStatus:
    def test_status_all_containers_healthy(
        self, registered_alice, monkeypatch, capsys
    ):
        import cli.status as st_script
        # Mock docker_ps to return both expected containers as "Up"
        monkeypatch.setattr(docker_ops, "docker_ps", lambda: [
            {"name": "myapp-user_alice-0-web", "status": "Up 2 minutes", "image": "nginx:alpine"},
            {"name": "myapp-user_alice-0-db", "status": "Up 2 minutes", "image": "postgres:16-alpine"},
        ])
        with patch.object(sys, "argv", ["cli/status.py", "-u", "alice"]):
            st_script.main()
        out = capsys.readouterr().out
        data = json.loads(out)
        user = data["user_status"][0]
        assert user["user_name"] == "alice"
        assert user["summary"]["healthy_services_#"] == 1
        assert user["summary"]["unhealthy_services_#"] == 0
        svc = user["healthy_services"][0]
        assert "myapp-user_alice-0-web" in svc["healthy_containers"]
        assert "myapp-user_alice-0-db" in svc["healthy_containers"]

    def test_status_missing_containers(
        self, registered_alice, monkeypatch, capsys
    ):
        import cli.status as st_script
        # No containers running
        monkeypatch.setattr(docker_ops, "docker_ps", lambda: [])
        with patch.object(sys, "argv", ["cli/status.py", "-u", "alice"]):
            st_script.main()
        out = capsys.readouterr().out
        data = json.loads(out)
        user = data["user_status"][0]
        assert user["summary"]["healthy_services_#"] == 0
        # Service should appear as missing (all containers absent)
        all_missing = user["missing_services"]
        assert len(all_missing) == 1
        assert "myapp-user_alice-0-web" in all_missing[0]["missing_containers"]

    def test_status_partial_unhealthy(
        self, registered_alice, monkeypatch, capsys
    ):
        import cli.status as st_script
        # Only web is up; db is missing
        monkeypatch.setattr(docker_ops, "docker_ps", lambda: [
            {"name": "myapp-user_alice-0-web", "status": "Up 5 minutes", "image": "nginx:alpine"},
        ])
        with patch.object(sys, "argv", ["cli/status.py", "-u", "alice"]):
            st_script.main()
        out = capsys.readouterr().out
        data = json.loads(out)
        user = data["user_status"][0]
        assert user["summary"]["healthy_services_#"] == 0
        assert user["summary"]["unhealthy_services_#"] == 1

    def test_status_unhealthy_container(
        self, registered_alice, monkeypatch, capsys
    ):
        import cli.status as st_script
        monkeypatch.setattr(docker_ops, "docker_ps", lambda: [
            {"name": "myapp-user_alice-0-web", "status": "Up 1 minute (unhealthy)", "image": "nginx:alpine"},
            {"name": "myapp-user_alice-0-db", "status": "Up 1 minute", "image": "postgres:16-alpine"},
        ])
        with patch.object(sys, "argv", ["cli/status.py", "-u", "alice"]):
            st_script.main()
        out = capsys.readouterr().out
        data = json.loads(out)
        user = data["user_status"][0]
        assert user["summary"]["healthy_services_#"] == 0
        svc = user["unhealthy_services"][0]
        assert "myapp-user_alice-0-web" in svc["unhealthy_containers"]

    def test_status_all_users_returned(
        self, tmp_path, mock_docker, monkeypatch, capsys
    ):
        import cli.register as reg_script
        import cli.status as st_script

        # Register alice and bob
        for user, label in [("alice", "0"), ("bob", "1")]:
            monkeypatch.setattr("getpass.getpass", lambda prompt="": "")
            monkeypatch.setattr("builtins.input", lambda prompt="": "y")
            sys_argv = [
                "cli/register.py",
                "-u", user, "-sn", "myapp",
                "-tc", COMPOSE_TEMPLATE,
                "-l", label,
                "-v", f"app_data=/data/{user}/app",
                "-v", f"db_data=/data/{user}/db",
            ]
            with patch.object(sys, "argv", sys_argv):
                reg_script.main()

        capsys.readouterr()  # flush registration output before checking status
        monkeypatch.setattr(docker_ops, "docker_ps", lambda: [])
        with patch.object(sys, "argv", ["cli/status.py"]):
            st_script.main()
        out = capsys.readouterr().out
        data = json.loads(out)
        names = [u["user_name"] for u in data["user_status"]]
        assert "alice" in names
        assert "bob" in names

    def test_status_unknown_user_fails(self, mock_docker, monkeypatch):
        import cli.status as st_script
        monkeypatch.setattr(docker_ops, "docker_ps", lambda: [])
        with patch.object(sys, "argv", ["cli/status.py", "-u", "ghost"]):
            with pytest.raises(SystemExit) as exc:
                st_script.main()
        assert exc.value.code != 0


# ---------------------------------------------------------------------------
# E2E: Full lifecycle  (register → status → rebuild → remove)
# ---------------------------------------------------------------------------

class TestE2EFullLifecycle:
    def test_full_lifecycle(
        self, tmp_path, mock_docker, monkeypatch, capsys
    ):
        import cli.register as reg_script
        import cli.rebuild as reb_script
        import cli.remove as rem_script
        import cli.status as st_script

        # 1. Register
        monkeypatch.setattr("getpass.getpass", lambda prompt="": "pass1")
        monkeypatch.setattr("builtins.input", lambda prompt="": "y")
        sys_argv = [
            "cli/register.py",
            "-u", "lifecycle_user", "-sn", "myapp",
            "-tc", COMPOSE_TEMPLATE,
            "-tn", NGINX_TEMPLATE,
            "-l", "5",
            "-d", "test.local",
            "-v", "app_data=/data/lc/app",
            "-v", "db_data=/data/lc/db",
        ]
        with patch.object(sys, "argv", sys_argv):
            reg_script.main()

        entry = reg_mod.get_user_service("lifecycle_user", "myapp", "5")
        assert entry is not None
        assert Path(entry["compose_file_path"]).exists()
        assert Path(entry["nginx_conf_path"]).exists()
        capsys.readouterr()  # flush registration output before checking status

        # 2. Status — healthy
        monkeypatch.setattr(docker_ops, "docker_ps", lambda: [
            {"name": "myapp-user_lifecycle_user-5-web", "status": "Up", "image": ""},
            {"name": "myapp-user_lifecycle_user-5-db", "status": "Up", "image": ""},
        ])
        with patch.object(sys, "argv", ["cli/status.py", "-u", "lifecycle_user"]):
            st_script.main()
        out = capsys.readouterr().out
        status_data = json.loads(out)
        assert status_data["user_status"][0]["summary"]["healthy_services_#"] == 1

        # 3. Rebuild with --no-cache
        mock_docker.reset_mock()
        sys_argv = [
            "cli/rebuild.py",
            "-u", "lifecycle_user", "-sn", "myapp", "-l", "5", "--no-cache",
        ]
        with patch.object(sys, "argv", sys_argv):
            reb_script.main()
        cmds = [c.args[0] for c in mock_docker.call_args_list if c.args]
        assert any("build" in cmd and "--no-cache" in cmd for cmd in cmds)

        # 4. Remove
        mock_docker.reset_mock()
        sys_argv = [
            "cli/remove.py",
            "-u", "lifecycle_user", "-sn", "myapp", "-l", "5",
        ]
        with patch.object(sys, "argv", sys_argv):
            rem_script.main()
        assert reg_mod.get_user_service("lifecycle_user", "myapp", "5") is None
        down_calls = [
            c for c in mock_docker.call_args_list
            if "down" in (c.args[0] if c.args else [])
        ]
        assert len(down_calls) >= 1
