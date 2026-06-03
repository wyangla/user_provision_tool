```
register.py -u alice -sn myapp
            -pr source_project/service_1
            -fc docker-compose.prod.yml
            -fn nginx.prod.conf
            -v app=/data/alice/app

All filenames (-tc/-fc/-tn/-fn) are resolved relative to -pr (project root).
Rendered compose files are written next to the template in the project root (-pr).
Nginx conf and .htpasswd files are written into GENERATED_DIR.

source_project/service_1/          ← project root (-pr)
  docker-compose.prod.yml          ← source compose  (-fc filename)
  nginx.prod.conf                  ← source nginx     (-fn filename)
  Dockerfile / src/                ← build context (build: . resolves here ✓)
      │
      ├─────────────────────────────────────────────────────────────────────┐
      ▼                                                                     ▼
┌─────────────────────────────────────────────────────────────┐  ┌─────────────────────────────────────────────────────────────┐
│ [0a/5]  compose_file_to_template()  (lib/compose_converter) │  │ [0b/5]  nginx_file_to_template()  (lib/nginx_converter)     │
│         only when -fc given; skipped if -tc given           │  │         only when -fn given; skipped if -tn given           │
│                                                             │  │                                                             │
│  docker-compose.prod.yml  ──parse──►  dict                  │  │  nginx.prod.conf  ──regex substitutions──►                  │
│                                   │                         │  │                                                             │
│                           transform:                        │  │  directive             before → after                      │
│                           • strip name:                     │  │  server_name           literal → {{ hostname }}             │
│                           • strip ports:                    │  │  auth_basic            literal → {{ service_name }} -       │
│                           • container_name → JINJA2TOKxxx   │  │                                   {{ user_name }}           │
│                           • strip profiles: (per service)   │  │                                                             │
│                           • exclude named-profile services  │  │                                                             │
│                           • bind sources  → JINJA2TOKxxx    │  │  auth_basic_user_file  path    → {{ htpasswd_path }}        │
│                           • networks      → JINJA2TOKxxx    │  │  proxy_pass            myapp-X → {{ container_prefix }}X   │
│                           • named volumes → JINJA2TOKxxx    │  │           (only host prefixed with service name hint)      │
│                                   │                         │  │                                                             │
│                          yaml.dump (no {{ }} quoting)       │  │       nginx.prod.conf.j2  ◄── written into -pr             │
│                                   │                         │  └─────────────────────────────────────────────────────────────┘
│                        detokenize (swap tokens → {{ }})     │                             │
│                                   │                         │                             │  nginx_template = nginx.prod.conf.j2
│       docker-compose.prod.yml.j2  ◄── written into -pr      │                             │  (or -tn filename if that was given)
└─────────────────────────────────────────────────────────────┘                             │
      │                                                                                     │
      │  compose_template = docker-compose.prod.yml.j2                                     │
      │  (or -tc filename if that was given)                                                │
      └─────────────────────────────────────────┬───────────────────────────────────────────┘
                                                ▼
┌─────────────────────────────────────────────────────────────┐
│ [1/5]  registry.add_user()                                  │
│                                                             │
│  writes user_registry.yml entry:                            │
│  { user_name, service_name, label,                          │
│    compose_template_path, nginx_conf_template_path,         │
│    volumes, … }                                             │
└─────────────────────────────────────────────────────────────┘
      │
      ▼
┌─────────────────────────────────────────────────────────────┐
│ [2/5]  template_engine.render_compose()                     │
│                                                             │
│  docker-compose.prod.yml.j2  ──Jinja2 (StrictUndefined)──► │
│                                                             │
│  context injected:                                          │
│  ┌────────────────────────────────────────────────────┐     │
│  │ user_name        = "alice"                         │     │
│  │ service_name     = "myapp"                         │     │
│  │ label            = "0"                             │     │
│  │ container_prefix = "myapp-user_alice-0-"           │     │
│  │ network_name     = "myapp-user_alice-0"            │     │
│  │ volumes          = { "app": "/data/alice/app" }    │     │
│  └────────────────────────────────────────────────────┘     │
│                                                             │
│  ┌─ template snippet ───────────────────────────────────┐   │
│  │ container_name: {{ container_prefix }}web            │   │
│  │ volumes:                                             │   │
│  │   - {{ volumes['app'] }}:/usr/share/nginx/html:ro   │   │
│  │ networks:                                            │   │
│  │   - {{ network_name }}                              │   │
│  └──────────────────────────────────────────────────────┘   │
│                          │                                  │
│                          ▼                                  │
│  ┌─ rendered output ────────────────────────────────────┐   │
│  │ container_name: myapp-user_alice-0-web               │   │
│  │ volumes:                                             │   │
│  │   - /data/alice/app:/usr/share/nginx/html:ro         │   │
│  │ networks:                                            │   │
│  │   - myapp-user_alice-0                              │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                             │
│  → docker-compose.user-alice.0.yml  ◄── written into -pr   │
│                                                             │
│  build: . resolves to project root (Dockerfile is there ✓) │
│  ${ENV_VAR} left as-is ── resolved by docker compose       │
│             via --env-file at runtime                       │
└─────────────────────────────────────────────────────────────┘
      │
      ▼
┌─────────────────────────────────────────────────────────────┐
│ [3/5]  template_engine.render_nginx_conf()                  │
│        (skipped entirely if neither -tn nor -fn given)      │
│                                                             │
│  nginx.prod.conf.j2  ──Jinja2 (StrictUndefined)──►         │
│                                                             │
│  same context as [2/5] plus:                                │
│  ┌────────────────────────────────────────────────────┐     │
│  │ domain_name  = "example.com"                       │     │
│  │ hostname     = "myapp-alice-0.example.com"         │     │
│  │ htpasswd_path = "GENERATED_DIR/myapp.user-alice    │     │
│  │                              .0.htpasswd"          │     │
│  └────────────────────────────────────────────────────┘     │
│                                                             │
│  ┌─ template snippet ───────────────────────────────────┐   │
│  │ server_name {{ hostname }};                          │   │
│  │ auth_basic_user_file {{ htpasswd_path }};            │   │
│  │ proxy_pass http://{{ container_prefix }}web:8080/;   │   │
│  └──────────────────────────────────────────────────────┘   │
│                          │                                  │
│                          ▼                                  │
│  ┌─ rendered output ────────────────────────────────────┐   │
│  │ server_name myapp-alice-0.example.com;               │   │
│  │ auth_basic_user_file .../myapp.user-alice.0.htpasswd;│   │
│  │ proxy_pass http://myapp-user_alice-0-web:8080/;      │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                             │
│  → myapp.user-alice.0.nginx.conf  ──► GENERATED_DIR        │
│  → myapp.user-alice.0.htpasswd    ──► GENERATED_DIR        │
│                                                             │
│  (when passwd='': auth_basic* lines stripped post-render;  │
│   no .htpasswd written; htpasswd_path=null in registry)    │
└─────────────────────────────────────────────────────────────┘
      │
      ▼
┌─────────────────────────────────────────────────────────────┐
│ [4/5]  docker_ops.compose_up()                              │
│                                                             │
│  docker compose -f /pr/docker-compose.user-alice.0.yml      │
│                 --project-name myapp-user_alice-0            │
│                 --env-file /pr/.env                          │
│                 up -d                                        │
│                                                             │
│  --project-name = network_name; prevents Compose from using  │
│  the source dir name as the project (which would cause all   │
│  users to share a project and tear down each other's         │
│  containers)                                                 │
│  full path passed; compose resolves build: . from there     │
│  --env-file is the copy placed next to the compose file     │
│  ${ENV_VAR} resolved here from --env-file at runtime        │
└─────────────────────────────────────────────────────────────┘
      │
      ▼
┌─────────────────────────────────────────────────────────────┐
│ [5/5]  docker_ops.network_connect() + nginx_reload()        │
│                                                             │
│  docker network connect myapp-user_alice-0 provision-nginx  │
│  docker exec provision-nginx nginx -s reload                │
│                                                             │
│  connects provision-nginx to the user's isolated network    │
│  so it can proxy traffic to the user's containers           │
└─────────────────────────────────────────────────────────────┘
```

Two distinct substitution phases:

- **Steps 0a–3** — `{{ var }}` Jinja2 expressions: registration-time, per-user values (names, paths, network, hostname)
- **Step 0b note** — if the source nginx conf has **no** `auth_basic` block, `nginx_converter` automatically injects `auth_basic "{{ service_name }} - {{ user_name }}";` and `auth_basic_user_file {{ htpasswd_path }};` before the first `proxy_pass`
- **Step 3 note** — when `passwd=''`, `render_nginx_conf()` strips all `auth_basic*` lines from the rendered output and skips writing the `.htpasswd` file; `htpasswd_path` is stored as `null` in the registry
- **Step 4** — `${VAR}` shell env vars: runtime secrets/config supplied via `--env-file`, shared across all users of the same service
- **Step 5** — post-compose networking: runs unconditionally; provision-nginx is connected to the new isolated network and reloaded

Flag summary (all filenames relative to `-pr`):

```
-pr  source_project/service_1   required — project root; all source files and
                                           rendered compose file live here

compose source  ──  -tc docker-compose.prod.yml.j2  (pre-made template)
                    -fc docker-compose.prod.yml      (auto-converted, step 0a)

nginx source    ──  -tn nginx.prod.conf.j2           (pre-made template, optional)
                    -fn nginx.prod.conf              (auto-converted, step 0b, optional)
```

Output directory summary:

```
-pr (project root)   docker-compose.prod.yml.j2      ← converted template (step 0a)
                     nginx.prod.conf.j2              ← converted template (step 0b)
                     docker-compose.user-alice.0.yml ← rendered compose   (step 2/5)

GENERATED_DIR        myapp.user-alice.0.nginx.conf   ← rendered nginx conf (step 3/5)
                     myapp.user-alice.0.htpasswd     ← bcrypt password     (step 3/5)
                     user_registry.yml               ← user registry       (step 1/5)
```

`build: .` in the compose file resolves to the project root where the Dockerfile lives.


