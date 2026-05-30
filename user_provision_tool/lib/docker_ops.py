"""Subprocess wrappers for docker compose commands.

Inside the deployed container, docker socket access is granted directly
(no sudo needed). On the host, use sudo externally or add user to docker group.
"""

from __future__ import annotations

import subprocess
import sys


def _run(args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    print(f"+ {' '.join(args)}", flush=True)
    result = subprocess.run(args, text=True, capture_output=False)
    if check and result.returncode != 0:
        raise RuntimeError(
            f"Command failed (exit {result.returncode}): {' '.join(args)}"
        )
    return result


def compose_up(compose_file: str, env_file: str | None = None) -> None:
    cmd = ["docker", "compose", "-f", compose_file]
    if env_file:
        cmd += ["--env-file", env_file]
    _run(cmd + ["up", "-d"])


def compose_down(compose_file: str, env_file: str | None = None) -> None:
    cmd = ["docker", "compose", "-f", compose_file]
    if env_file:
        cmd += ["--env-file", env_file]
    _run(cmd + ["down"])


def compose_build(compose_file: str, no_cache: bool = False, env_file: str | None = None) -> None:
    cmd = ["docker", "compose", "-f", compose_file]
    if env_file:
        cmd += ["--env-file", env_file]
    cmd += ["build"]
    if no_cache:
        cmd.append("--no-cache")
    _run(cmd)


def docker_ps() -> list[dict[str, str]]:
    """Return list of running containers as dicts with keys: name, status, image."""
    result = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}\t{{.Status}}\t{{.Image}}"],
        text=True,
        capture_output=True,
    )
    containers = []
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            containers.append({
                "name": parts[0].strip(),
                "status": parts[1].strip(),
                "image": parts[2].strip() if len(parts) > 2 else "",
            })
    return containers


def docker_stats_snapshot() -> list[dict[str, str]]:
    """Return a one-shot snapshot of docker stats (no-stream)."""
    result = subprocess.run(
        [
            "docker", "stats", "--no-stream",
            "--format", "{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}",
        ],
        text=True,
        capture_output=True,
    )
    stats = []
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3:
            stats.append({
                "name": parts[0].strip(),
                "cpu": parts[1].strip(),
                "mem": parts[2].strip(),
            })
    return stats
