"""Unit tests for individual lib/ modules."""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from lib import auth, docker_ops, registry, template_engine, validation

FIXTURES_DIR = Path(__file__).parent / "fixtures"
COMPOSE_TEMPLATE = str(FIXTURES_DIR / "docker-compose.template.yml.j2")
NGINX_TEMPLATE = str(FIXTURES_DIR / "myapp.template.nginx.conf.j2")

# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------


class TestValidation:
    def test_valid_name(self):
        assert validation.validate_name("alice_1", "user_name") == "alice_1"

    def test_valid_name_all_chars(self):
        assert validation.validate_name("Service_Name123") == "Service_Name123"

    @pytest.mark.parametrize("bad", ["alice!", "alice-1", "alice 1", "user@host", ""])
    def test_invalid_name(self, bad):
        with pytest.raises(validation.ValidationError):
            validation.validate_name(bad, "user_name")

    def test_empty_name_raises(self):
        with pytest.raises(validation.ValidationError, match="must not be empty"):
            validation.validate_name("")

    def test_valid_label(self):
        assert validation.validate_label("0") == "0"
        assert validation.validate_label("42") == "42"

    @pytest.mark.parametrize("bad", ["a", "1a", "-1", "1.0", ""])
    def test_invalid_label(self, bad):
        with pytest.raises(validation.ValidationError):
            validation.validate_label(bad)


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_empty_registry(self, registry_file):
        assert registry.get_all_users() == []

    def test_add_and_get_user(self, registry_file, sample_entry):
        registry.add_user(sample_entry)
        users = registry.get_all_users()
        assert len(users) == 1
        assert users[0]["user_name"] == "alice"

    def test_get_user_by_name(self, registry_file, sample_entry):
        registry.add_user(sample_entry)
        result = registry.get_user("alice")
        assert len(result) == 1
        assert result[0]["service_name"] == "myapp"

    def test_get_user_unknown(self, registry_file, sample_entry):
        registry.add_user(sample_entry)
        assert registry.get_user("nobody") == []

    def test_get_user_service_exact_match(self, registry_file, sample_entry):
        registry.add_user(sample_entry)
        found = registry.get_user_service("alice", "myapp", "0")
        assert found is not None
        assert found["label"] == "0"

    def test_get_user_service_no_match(self, registry_file, sample_entry):
        registry.add_user(sample_entry)
        assert registry.get_user_service("alice", "myapp", "99") is None

    def test_remove_user_service(self, registry_file, sample_entry):
        registry.add_user(sample_entry)
        removed = registry.remove_user_service("alice", "myapp", "0")
        assert removed is True
        assert registry.get_all_users() == []

    def test_remove_nonexistent_returns_false(self, registry_file):
        assert registry.remove_user_service("ghost", "svc", "0") is False

    def test_multiple_users_isolated(self, registry_file, sample_entry):
        entry_bob = dict(sample_entry, user_name="bob")
        registry.add_user(sample_entry)
        registry.add_user(entry_bob)
        registry.remove_user_service("alice", "myapp", "0")
        remaining = registry.get_all_users()
        assert len(remaining) == 1
        assert remaining[0]["user_name"] == "bob"

    def test_registry_persists_to_yaml(self, registry_file, sample_entry):
        registry.add_user(sample_entry)
        raw = yaml.safe_load(registry_file.read_text())
        assert isinstance(raw, list)
        assert raw[0]["user_name"] == "alice"


# ---------------------------------------------------------------------------
# auth
# ---------------------------------------------------------------------------


class TestAuth:
    def test_hash_password_produces_bcrypt_hash(self):
        h = auth.hash_password("alice", "secret123")
        assert h.startswith("$2")

    def test_hash_password_empty_returns_empty(self):
        assert auth.hash_password("alice", "") == ""

    def test_hash_different_passwords_differ(self):
        h1 = auth.hash_password("alice", "password1")
        h2 = auth.hash_password("alice", "password2")
        assert h1 != h2

    def test_write_htpasswd_file(self, tmp_path):
        path = str(tmp_path / "test.htpasswd")
        h = auth.hash_password("alice", "secret")
        auth.write_htpasswd_file(path, "alice", h)
        content = Path(path).read_text()
        assert content.startswith("alice:$2")
        assert content.endswith("\n")

    def test_prompt_password_returns_empty_on_blank(self, monkeypatch):
        monkeypatch.setattr("getpass.getpass", lambda prompt="": "")
        result = auth.prompt_password("alice")
        assert result == ""

    def test_prompt_password_mismatch_raises(self, monkeypatch):
        responses = iter(["abc", "xyz"])
        monkeypatch.setattr("getpass.getpass", lambda prompt="": next(responses))
        with pytest.raises(ValueError, match="do not match"):
            auth.prompt_password("alice")

    def test_prompt_password_match_returns_value(self, monkeypatch):
        monkeypatch.setattr("getpass.getpass", lambda prompt="": "mysecret")
        result = auth.prompt_password("alice")
        assert result == "mysecret"


# ---------------------------------------------------------------------------
# template_engine
# ---------------------------------------------------------------------------


class TestTemplateEngine:
    def test_container_prefix(self):
        assert template_engine.container_prefix("myapp", "alice", "0") == "myapp-user_alice-0-"

    def test_extract_template_volumes_top_level(self):
        vols = template_engine.extract_template_volumes(COMPOSE_TEMPLATE)
        # db_socket is declared as a top-level named volume
        assert "db_socket" in vols

    def test_extract_template_volumes_bind_mounts(self):
        vols = template_engine.extract_template_volumes(COMPOSE_TEMPLATE)
        # app_data and db_data are referenced via {{ volumes['app_data'] }} /
        # {{ volumes['db_data'] }} Jinja2 expressions and are now detected.
        assert "app_data" in vols
        assert "db_data" in vols

    def test_render_compose(self, tmp_path):
        out = str(tmp_path / "docker-compose.user-alice.0.yml")
        template_engine.render_compose(
            COMPOSE_TEMPLATE, out,
            user_name="alice", service_name="myapp", label="0",
            volumes={"app_data": "/srv/alice/app", "db_data": "/srv/alice/db"},
        )
        content = Path(out).read_text()
        data = yaml.safe_load(content)
        services = data["services"]
        assert "web" in services
        assert "db" in services
        assert services["web"]["container_name"] == "myapp-user_alice-0-web"
        assert services["db"]["container_name"] == "myapp-user_alice-0-db"
        # Volumes are correctly substituted
        assert "/srv/alice/app" in content
        assert "/srv/alice/db" in content

    def test_render_compose_env_vars(self, tmp_path):
        out = str(tmp_path / "docker-compose.user-bob.1.yml")
        template_engine.render_compose(
            COMPOSE_TEMPLATE, out,
            user_name="bob", service_name="myapp", label="1",
            volumes={"app_data": "/data/bob/app", "db_data": "/data/bob/db"},
        )
        content = Path(out).read_text()
        assert "USER_NAME=bob" in content
        assert "SERVICE_NAME=myapp" in content
        assert "LABEL=1" in content

    def test_render_nginx_conf(self, tmp_path):
        out = str(tmp_path / "myapp.user-alice.0.nginx.conf")
        htpasswd = str(tmp_path / "myapp.user-alice.0.htpasswd")
        template_engine.render_nginx_conf(
            NGINX_TEMPLATE, out,
            user_name="alice", service_name="myapp", label="0",
            domain_name="example.com", htpasswd_path=htpasswd,
        )
        content = Path(out).read_text()
        assert "server_name myapp-alice-0.example.com;" in content
        assert f"auth_basic_user_file {htpasswd};" in content
        assert "proxy_pass         http://myapp-user_alice-0-web:80;" in content

    def test_render_nginx_hostname_format(self, tmp_path):
        out = str(tmp_path / "out.conf")
        template_engine.render_nginx_conf(
            NGINX_TEMPLATE, out,
            user_name="testuser", service_name="svc", label="3",
            domain_name="test.local", htpasswd_path="/tmp/test.htpasswd",
        )
        content = Path(out).read_text()
        assert "server_name svc-testuser-3.test.local;" in content

    def test_render_compose_two_users_independent(self, tmp_path):
        out_alice = str(tmp_path / "alice.yml")
        out_bob = str(tmp_path / "bob.yml")
        template_engine.render_compose(
            COMPOSE_TEMPLATE, out_alice,
            "alice", "myapp", "0", {"app_data": "/a/app", "db_data": "/a/db"},
        )
        template_engine.render_compose(
            COMPOSE_TEMPLATE, out_bob,
            "bob", "myapp", "0", {"app_data": "/b/app", "db_data": "/b/db"},
        )
        c_alice = yaml.safe_load(Path(out_alice).read_text())
        c_bob = yaml.safe_load(Path(out_bob).read_text())
        assert c_alice["services"]["web"]["container_name"] == "myapp-user_alice-0-web"
        assert c_bob["services"]["web"]["container_name"] == "myapp-user_bob-0-web"

    # --- network_name helper ---

    def test_user_network_name_format(self):
        assert template_engine.user_network_name("myapp", "alice", "0") == "myapp-user_alice-0"

    def test_user_network_name_different_users_differ(self):
        n1 = template_engine.user_network_name("myapp", "alice", "0")
        n2 = template_engine.user_network_name("myapp", "bob", "0")
        assert n1 != n2

    def test_user_network_name_different_labels_differ(self):
        n1 = template_engine.user_network_name("myapp", "alice", "0")
        n2 = template_engine.user_network_name("myapp", "alice", "1")
        assert n1 != n2

    # --- network_name in rendered compose ---

    def test_render_compose_declares_named_network(self, tmp_path):
        out = str(tmp_path / "dc.yml")
        template_engine.render_compose(
            COMPOSE_TEMPLATE, out,
            user_name="alice", service_name="myapp", label="0",
            volumes={"app_data": "/srv/alice/app", "db_data": "/srv/alice/db"},
        )
        data = yaml.safe_load(Path(out).read_text())
        expected_net = "myapp-user_alice-0"
        assert "networks" in data, "Top-level 'networks' key missing from rendered compose"
        assert expected_net in data["networks"], f"Network '{expected_net}' not declared"
        assert data["networks"][expected_net]["name"] == expected_net

    def test_render_compose_services_joined_to_network(self, tmp_path):
        out = str(tmp_path / "dc.yml")
        template_engine.render_compose(
            COMPOSE_TEMPLATE, out,
            user_name="alice", service_name="myapp", label="0",
            volumes={"app_data": "/srv/alice/app", "db_data": "/srv/alice/db"},
        )
        data = yaml.safe_load(Path(out).read_text())
        expected_net = "myapp-user_alice-0"
        for svc_name, svc in data["services"].items():
            assert "networks" in svc, f"Service '{svc_name}' missing 'networks' key"
            assert expected_net in svc["networks"], (
                f"Service '{svc_name}' not joined to network '{expected_net}'"
            )

    def test_render_compose_two_users_have_different_networks(self, tmp_path):
        out_alice = str(tmp_path / "alice.yml")
        out_bob = str(tmp_path / "bob.yml")
        template_engine.render_compose(
            COMPOSE_TEMPLATE, out_alice,
            "alice", "myapp", "0", {"app_data": "/a/app", "db_data": "/a/db"},
        )
        template_engine.render_compose(
            COMPOSE_TEMPLATE, out_bob,
            "bob", "myapp", "0", {"app_data": "/b/app", "db_data": "/b/db"},
        )
        d_alice = yaml.safe_load(Path(out_alice).read_text())
        d_bob = yaml.safe_load(Path(out_bob).read_text())
        nets_alice = set(d_alice["networks"].keys())
        nets_bob = set(d_bob["networks"].keys())
        assert nets_alice.isdisjoint(nets_bob), "Two different users must not share a network"


# ---------------------------------------------------------------------------
# docker_ops
# ---------------------------------------------------------------------------


class TestDockerOps:
    """Unit tests for docker_ops helper functions (subprocess patched)."""

    def _mock_run(self, monkeypatch):
        """Patch subprocess.run inside docker_ops and return a call-list."""
        calls: list[list[str]] = []

        def fake_run(args, **kwargs):
            calls.append(list(args))
            m = MagicMock()
            m.returncode = 0
            return m

        monkeypatch.setattr(docker_ops.subprocess, "run", fake_run)
        return calls

    def test_network_connect_command(self, monkeypatch):
        calls = self._mock_run(monkeypatch)
        docker_ops.network_connect("provision-nginx", "myapp-user_alice-0")
        assert calls[-1] == [
            "docker", "network", "connect", "myapp-user_alice-0", "provision-nginx"
        ]

    def test_network_disconnect_command(self, monkeypatch):
        calls = self._mock_run(monkeypatch)
        docker_ops.network_disconnect("provision-nginx", "myapp-user_alice-0")
        assert calls[-1] == [
            "docker", "network", "disconnect", "myapp-user_alice-0", "provision-nginx"
        ]

    def test_nginx_reload_command(self, monkeypatch):
        calls = self._mock_run(monkeypatch)
        docker_ops.nginx_reload("provision-nginx")
        assert calls[-1] == [
            "docker", "exec", "provision-nginx", "nginx", "-s", "reload"
        ]

    def test_network_connect_uses_check_false(self, monkeypatch):
        """network_connect must not raise even when docker returns non-zero."""
        def fail_run(args, **kwargs):
            m = MagicMock()
            m.returncode = 1
            return m
        monkeypatch.setattr(docker_ops.subprocess, "run", fail_run)
        # Should not raise
        docker_ops.network_connect("provision-nginx", "nonexistent-net")

    def test_network_disconnect_uses_check_false(self, monkeypatch):
        """network_disconnect must not raise even when docker returns non-zero."""
        def fail_run(args, **kwargs):
            m = MagicMock()
            m.returncode = 1
            return m
        monkeypatch.setattr(docker_ops.subprocess, "run", fail_run)
        docker_ops.network_disconnect("provision-nginx", "nonexistent-net")

    def test_nginx_reload_uses_check_false(self, monkeypatch):
        """nginx_reload must not raise even when the container is absent."""
        def fail_run(args, **kwargs):
            m = MagicMock()
            m.returncode = 1
            return m
        monkeypatch.setattr(docker_ops.subprocess, "run", fail_run)
        docker_ops.nginx_reload("provision-nginx")


# ---------------------------------------------------------------------------
# compose_converter
# ---------------------------------------------------------------------------

_SAMPLE_NGINX_CONF = """\
server {
    listen 80;
    server_name myapp.example.com;

    auth_basic "My App";
    auth_basic_user_file /etc/nginx/htpasswd/myapp;

    location / {
        proxy_pass http://myapp-web:80;
        proxy_set_header Host $host;
    }
}
"""


class TestComposeConverter:
    """Unit tests for lib/compose_converter module."""

    def _sample_data(self) -> dict:
        return {
            "name": "myapp",
            "services": {
                "web": {
                    "image": "nginx:alpine",
                    "container_name": "myapp-web",
                    "ports": ["80:80"],
                    "volumes": ["/data/myapp/html:/usr/share/nginx/html:ro"],
                    "networks": ["mynet"],
                },
                "db": {
                    "image": "postgres:16",
                    "container_name": "myapp-db",
                    "volumes": [
                        "/var/lib/myapp/db:/var/lib/postgresql/data",
                        "db_socket:/var/run/postgresql",
                    ],
                    "networks": ["mynet"],
                },
            },
            "volumes": {"db_socket": None},
            "networks": {"mynet": None},
        }

    def _convert_to_text(self, data: dict) -> tuple[str, dict]:
        """Run convert() and return (detokenized_yaml_text, src_to_key)."""
        from lib.compose_converter import convert
        transformed, src_to_key, tokens = convert(data)
        raw = yaml.dump(transformed, default_flow_style=False, sort_keys=False)
        return tokens.detokenize(raw), src_to_key

    def test_convert_strips_name(self):
        from lib.compose_converter import convert
        transformed, _, _ = convert(self._sample_data())
        assert "name" not in transformed

    def test_convert_strips_ports(self):
        from lib.compose_converter import convert
        transformed, _, _ = convert(self._sample_data())
        assert "ports" not in transformed["services"]["web"]

    def test_convert_sets_container_name_template(self):
        text, _ = self._convert_to_text(self._sample_data())
        assert "{{ container_prefix }}web" in text
        assert "{{ container_prefix }}db" in text

    def test_convert_replaces_bind_mounts(self):
        text, _ = self._convert_to_text(self._sample_data())
        assert "{{ volumes['" in text
        assert "/data/myapp/html" not in text
        assert "/var/lib/myapp/db" not in text

    def test_convert_src_to_key_bind_mounts(self):
        from lib.compose_converter import convert
        _, src_to_key, _ = convert(self._sample_data())
        assert "/data/myapp/html" in src_to_key
        assert "/var/lib/myapp/db" in src_to_key

    def test_convert_named_volumes_excluded_from_src_to_key(self):
        from lib.compose_converter import convert
        _, src_to_key, _ = convert(self._sample_data())
        assert "db_socket" not in src_to_key

    def test_convert_replaces_networks(self):
        text, _ = self._convert_to_text(self._sample_data())
        assert "{{ network_name }}" in text

    def test_convert_adds_named_volume_prefix(self):
        text, _ = self._convert_to_text(self._sample_data())
        assert "{{ container_prefix }}db_socket" in text

    def test_convert_preserves_env_vars(self):
        data = self._sample_data()
        data["services"]["web"]["environment"] = ["MY_VAR=${MY_ENV_VAR}"]
        text, _ = self._convert_to_text(data)
        assert "${MY_ENV_VAR}" in text

    def test_convert_external_volume_unchanged(self):
        data = self._sample_data()
        data["volumes"]["shared_vol"] = {"external": True}
        from lib.compose_converter import convert
        transformed, _, tokens = convert(data)
        raw = yaml.dump(transformed, default_flow_style=False, sort_keys=False)
        text = tokens.detokenize(raw)
        # External volumes must NOT get a container_prefix name override
        assert "{{ container_prefix }}shared_vol" not in text

    def test_compose_file_to_template_creates_file(self, tmp_path):
        from lib.compose_converter import compose_file_to_template
        src = str(FIXTURES_DIR / "docker-compose.plain.yml")
        out = str(tmp_path / "output.yml.j2")
        result = compose_file_to_template(src, out, "myapp")
        assert Path(out).exists()
        assert isinstance(result, dict)

    def test_compose_file_to_template_returns_bind_mount_keys(self, tmp_path):
        from lib.compose_converter import compose_file_to_template
        src = str(FIXTURES_DIR / "docker-compose.plain.yml")
        out = str(tmp_path / "output.yml.j2")
        src_to_key = compose_file_to_template(src, out, "myapp")
        assert len(src_to_key) > 0

    def test_compose_file_to_template_content_has_jinja2(self, tmp_path):
        from lib.compose_converter import compose_file_to_template
        src = str(FIXTURES_DIR / "docker-compose.plain.yml")
        out = str(tmp_path / "output.yml.j2")
        compose_file_to_template(src, out, "myapp")
        content = Path(out).read_text()
        assert "{{ container_prefix }}" in content
        assert "{{ network_name }}" in content

    def test_compose_file_to_template_strips_ports(self, tmp_path):
        from lib.compose_converter import compose_file_to_template
        src = str(FIXTURES_DIR / "docker-compose.plain.yml")
        out = str(tmp_path / "output.yml.j2")
        compose_file_to_template(src, out, "myapp")
        content = Path(out).read_text()
        # Strip header comments before checking — the comment mentions "ports:" deliberately
        yaml_body = "\n".join(
            line for line in content.splitlines() if not line.startswith("#")
        )
        assert "ports:" not in yaml_body

    def test_compose_file_to_template_invalid_raises(self, tmp_path):
        from lib.compose_converter import compose_file_to_template
        bad = str(tmp_path / "bad.yml")
        Path(bad).write_text("just: scalar\n")
        out = str(tmp_path / "out.yml.j2")
        with pytest.raises(ValueError, match="services"):
            compose_file_to_template(bad, out)

    def test_make_header_contains_volume_keys(self):
        from lib.compose_converter import make_header
        src_to_key = {"/data/app": "app", "/data/db": "db"}
        header = make_header(src_to_key, "myapp")
        assert "app" in header
        assert "db" in header
        assert "-v app=/your/path" in header

    def test_make_header_no_volumes(self):
        from lib.compose_converter import make_header
        header = make_header({}, "myapp")
        assert "myapp.yml.j2" in header
        assert "{{ container_prefix }}" in header

    def test_unique_keys_for_duplicate_basenames(self):
        """Two bind mounts with the same basename get distinct volume keys."""
        from lib.compose_converter import convert
        data = {
            "services": {
                "a": {"image": "x", "volumes": ["/alpha/data:/a"]},
                "b": {"image": "x", "volumes": ["/beta/data:/b"]},
            }
        }
        _, src_to_key, _ = convert(data)
        keys = list(src_to_key.values())
        assert len(keys) == len(set(keys)), "Duplicate volume keys generated"


# ---------------------------------------------------------------------------
# nginx_converter
# ---------------------------------------------------------------------------


class TestNginxConverter:
    """Unit tests for lib/nginx_converter module."""

    def test_convert_server_name(self):
        from lib.nginx_converter import convert_nginx
        out = convert_nginx(_SAMPLE_NGINX_CONF)
        assert "server_name {{ hostname }};" in out
        assert "myapp.example.com" not in out

    def test_convert_auth_basic(self):
        from lib.nginx_converter import convert_nginx
        out = convert_nginx(_SAMPLE_NGINX_CONF)
        assert 'auth_basic "{{ service_name }} - {{ user_name }}";' in out

    def test_convert_auth_basic_user_file(self):
        from lib.nginx_converter import convert_nginx
        out = convert_nginx(_SAMPLE_NGINX_CONF)
        assert "auth_basic_user_file {{ htpasswd_path }};" in out

    def test_convert_proxy_pass_with_hint(self):
        from lib.nginx_converter import convert_nginx
        out = convert_nginx(_SAMPLE_NGINX_CONF, service_name_hint="myapp")
        assert "proxy_pass http://{{ container_prefix }}web:80;" in out
        assert "myapp-web" not in out

    def test_convert_proxy_pass_without_hint(self):
        from lib.nginx_converter import convert_nginx
        out = convert_nginx(_SAMPLE_NGINX_CONF)
        # Without a service_name_hint, proxy_pass target is not rewritten
        assert "proxy_pass http://myapp-web:80;" in out

    def test_convert_preserves_proxy_headers(self):
        from lib.nginx_converter import convert_nginx
        out = convert_nginx(_SAMPLE_NGINX_CONF)
        assert "proxy_set_header Host $host;" in out

    def test_convert_no_auth_basic_untouched_when_absent(self):
        from lib.nginx_converter import convert_nginx
        conf = "server { listen 80; server_name example.com; }\n"
        out = convert_nginx(conf)
        assert "auth_basic" not in out

    def test_nginx_file_to_template_creates_file(self, tmp_path):
        from lib.nginx_converter import nginx_file_to_template
        src = str(FIXTURES_DIR / "myapp.plain.nginx.conf")
        out = str(tmp_path / "myapp.nginx.conf.j2")
        nginx_file_to_template(src, out, "myapp")
        assert Path(out).exists()

    def test_nginx_file_to_template_has_header(self, tmp_path):
        from lib.nginx_converter import nginx_file_to_template
        src = str(FIXTURES_DIR / "myapp.plain.nginx.conf")
        out = str(tmp_path / "myapp.nginx.conf.j2")
        nginx_file_to_template(src, out, "myapp")
        content = Path(out).read_text()
        assert "generated by gen_nginx_template.py" in content

    def test_nginx_file_to_template_transforms_content(self, tmp_path):
        from lib.nginx_converter import nginx_file_to_template
        src = str(FIXTURES_DIR / "myapp.plain.nginx.conf")
        out = str(tmp_path / "out.j2")
        nginx_file_to_template(src, out, "myapp")
        content = Path(out).read_text()
        assert "{{ hostname }}" in content
        assert "{{ htpasswd_path }}" in content
        assert "{{ container_prefix }}" in content

    def test_make_header_includes_hint(self):
        from lib.nginx_converter import make_header
        header = make_header("myapp.nginx.conf", "myapp")
        assert "myapp" in header
        assert "generated by gen_nginx_template.py" in header

    def test_make_header_lists_template_vars(self):
        from lib.nginx_converter import make_header
        header = make_header("test.conf", "svc")
        assert "{{ hostname }}" in header
        assert "{{ container_prefix }}" in header
        assert "{{ htpasswd_path }}" in header


# ---------------------------------------------------------------------------
# provisioner
# ---------------------------------------------------------------------------


class TestProvisioner:
    """Unit tests for lib/provisioner helper functions."""

    def test_auto_volumes_creates_directories(self, tmp_path):
        """_auto_volumes creates a subdirectory for each volume key."""
        from lib.provisioner import _auto_volumes
        result = _auto_volumes(COMPOSE_TEMPLATE, "alice", "myapp", "0", tmp_path / "ud")
        for key, path in result.items():
            assert Path(path).is_dir(), f"Expected dir for volume '{key}': {path}"

    def test_auto_volumes_paths_rooted_at_user_data_dir(self, tmp_path):
        """Returned paths sit under user_data_dir/{user}/{service}/{label}/{key}."""
        from lib.provisioner import _auto_volumes
        user_data = tmp_path / "user_data"
        result = _auto_volumes(COMPOSE_TEMPLATE, "alice", "myapp", "0", user_data)
        base = user_data / "alice" / "myapp" / "0"
        for key, path in result.items():
            assert path == str(base / key)

    def test_auto_volumes_detects_jinja2_dict_keys(self, tmp_path):
        """Keys referenced via {{ volumes['key'] }} in the template are detected."""
        from lib.provisioner import _auto_volumes
        result = _auto_volumes(COMPOSE_TEMPLATE, "alice", "myapp", "0", tmp_path)
        # Fixture template uses {{ volumes['app_data'] }} and {{ volumes['db_data'] }}
        assert "app_data" in result
        assert "db_data" in result

    def test_auto_volumes_idempotent(self, tmp_path):
        """Calling _auto_volumes twice for the same user does not raise."""
        from lib.provisioner import _auto_volumes
        user_data = tmp_path / "user_data"
        _auto_volumes(COMPOSE_TEMPLATE, "alice", "myapp", "0", user_data)
        _auto_volumes(COMPOSE_TEMPLATE, "alice", "myapp", "0", user_data)  # must not raise
