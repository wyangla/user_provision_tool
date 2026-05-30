"""Password prompt and htpasswd hashing via passlib."""

from __future__ import annotations

import getpass

from passlib.hash import bcrypt as _bcrypt_hasher


def prompt_password(user_name: str) -> str:
    """Interactively prompt for a password. Returns empty string if skipped."""
    try:
        passwd = getpass.getpass(f"Password for '{user_name}' (leave blank to skip): ")
    except (EOFError, KeyboardInterrupt):
        return ""
    if passwd:
        confirm = getpass.getpass("Confirm password: ")
        if passwd != confirm:
            raise ValueError("Passwords do not match.")
    return passwd


def hash_password(user_name: str, passwd: str) -> str:
    """Return a bcrypt hash for the given password (htpasswd-compatible $2b$ format).
    Returns an empty string if passwd is empty."""
    if not passwd:
        return ""
    return _bcrypt_hasher.using(rounds=12).hash(passwd)


def write_htpasswd_file(path: str, user_name: str, passwd_hash: str) -> None:
    """Write a .htpasswd file with a pre-hashed password entry."""
    with open(path, "w") as f:
        f.write(f"{user_name}:{passwd_hash}\n")
