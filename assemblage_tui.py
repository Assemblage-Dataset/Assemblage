#!/usr/bin/env python3
"""Assemblage TUI - minimal keyboard-driven interface."""

import getpass
import json
import re
import subprocess
import sys
import termios
import time
import tty
from dataclasses import asdict, dataclass
from pathlib import Path

try:
    from rich.console import Console
except ImportError:
    print("rich is required: pip install rich")
    sys.exit(1)

console = Console()

LOGO = r"""
     _                          _     _
    / \   ___ ___  ___ _ __ ___| |__ | | __ _  __ _  ___
   / _ \ / __/ __|/ _ \ '_ ` _ \ '_ \| |/ _` |/ _` |/ _ \
  / ___ \\__ \__ \  __/ | | | | | |_) | | (_| | (_| |  __/
 /_/   \_\___/___/\___|_| |_| |_|_.__/|_|\__,_|\__, |\___|
                                                |___/
"""

PROJECT_ROOT = Path(__file__).parent.resolve()
COMPOSE_FILE = "docker-compose-tui.yml"
SECRETS_FILE = "secrets.env"
CONFIG_FILE = ".assemblage-tui.json"


# ── Terminal input ───────────────────────────────────────────────────────────────


def _getch():
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == "\x1b":
            ch += sys.stdin.read(2)
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def select(title, options):
    """Arrow-key menu. Returns index."""
    cur = 0
    while True:
        console.clear()
        console.print(f"[bold cyan]{LOGO}[/bold cyan]")
        console.print(f"  [bold]{title}[/bold]\n")
        for i, opt in enumerate(options):
            if i == cur:
                console.print(f"  [bold cyan]> {opt}[/bold cyan]")
            else:
                console.print(f"    [dim]{opt}[/dim]")
        console.print()
        key = _getch()
        if key == "\x1b[A":
            cur = (cur - 1) % len(options)
        elif key == "\x1b[B":
            cur = (cur + 1) % len(options)
        elif key in ("\r", "\n"):
            return cur
        elif key in ("q", "\x03"):
            return len(options) - 1


def pause():
    input("\n  Press enter to continue...")


# ── Config ───────────────────────────────────────────────────────────────────────


@dataclass
class TUIConfig:
    github_token: str = ""
    db_password: str = "assemblage"
    s3_user: str = "minioadmin"
    s3_pass: str = "minioadmin"
    gcc_enabled: bool = True
    gcc_count: int = 1
    clang_enabled: bool = True
    clang_count: int = 1
    opt_none: bool = True
    opt_low: bool = True
    opt_medium: bool = True
    opt_high: bool = True
    restart_interval_hours: int = 0  # 0 = no auto-restart

    def save(self) -> Path:
        path = PROJECT_ROOT / CONFIG_FILE
        path.write_text(json.dumps(asdict(self), indent=2) + "\n")
        return path

    @classmethod
    def load(cls) -> "TUIConfig":
        path = PROJECT_ROOT / CONFIG_FILE
        cfg = cls()
        if path.exists():
            try:
                for k, v in json.loads(path.read_text()).items():
                    if hasattr(cfg, k):
                        setattr(cfg, k, v)
            except (json.JSONDecodeError, OSError):
                pass
        return cfg


# ── File generation ──────────────────────────────────────────────────────────────


def write_secrets_env(cfg: TUIConfig) -> Path:
    path = PROJECT_ROOT / SECRETS_FILE
    path.write_text(
        "\n".join(
            [
                "DB_HOST=assemblage-db",
                "DB_PORT=5432",
                "POSTGRES_DATABASE=assemblage",
                "POSTGRES_USER=assemblage",
                f"POSTGRES_PASSWORD={cfg.db_password}",
                f"GITHUB_TOKEN={cfg.github_token}",
                f"S3_ACCESS_KEY={cfg.s3_user}",
                f"S3_SECRET_ACCESS_KEY={cfg.s3_pass}",
                f"MINIO_ROOT_USER={cfg.s3_user}",
                f"MINIO_ROOT_PASSWORD={cfg.s3_pass}",
                "S3_HOST=minio",
                "S3_HTTPS=false",
            ]
        )
        + "\n"
    )
    return path


def write_compose_file(cfg: TUIConfig) -> Path:
    path = PROJECT_ROOT / COMPOSE_FILE
    builders = ""
    idx = 0
    compilers = []
    if cfg.gcc_enabled:
        compilers.append(("gcc", cfg.gcc_count, "docker/gcc/Dockerfile", "assemblage-gcc:default"))
    if cfg.clang_enabled:
        compilers.append(
            ("clang", cfg.clang_count, "docker/clang/Dockerfile", "assemblage-clang:default")
        )

    for compiler, count, dockerfile, image in compilers:
        for i in range(count):
            builders += f"""
  builder_{idx}:
    image: {image}
    build:
      context: .
      dockerfile: {dockerfile}
    environment:
      PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION: python
      PYTHONUNBUFFERED: 1
      TYPE: builder
      compiler: {compiler}
      language: "c++"
      name: {compiler}-builder-{i}
    env_file:
      - {SECRETS_FILE}
    depends_on:
      rabbitmq:
        condition: service_healthy
      minio:
        condition: service_healthy
    deploy:
      restart_policy:
        condition: on-failure
    volumes:
      - ./backend:/app
"""
            idx += 1

    path.write_text(f"""\
services:

  database:
    container_name: assemblage-db
    image: postgres:18.0-alpine3.22
    env_file:
      - {SECRETS_FILE}
    environment:
      - POSTGRES_USER=assemblage
    volumes:
      - db-data:/var/lib/postgresql
    healthcheck:
      test: ["CMD", "pg_isready", "-U", "assemblage"]
      interval: 10s
      timeout: 5s
      retries: 5

  rabbitmq:
    image: rabbitmq:3-management
    environment:
      RABBITMQ_SERVER_ADDITIONAL_ERL_ARGS: -rabbit consumer_timeout 900000
    healthcheck:
      test: ["CMD", "rabbitmq-diagnostics", "-q", "check_running"]
      interval: 5s
      timeout: 10s
      retries: 25
      start_period: 10s
    volumes:
      - rabbitmq-data:/var/lib/rabbitmq
    ports:
      - 5672:5672

  coordinator:
    image: assemblage-gcc:default
    build:
      context: .
      dockerfile: docker/gcc/Dockerfile
    environment:
      PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION: python
      PYTHONUNBUFFERED: 1
      TYPE: coordinator
      name: coordinator
    env_file:
      - {SECRETS_FILE}
    depends_on:
      database:
        condition: service_healthy
      rabbitmq:
        condition: service_healthy
      minio:
        condition: service_healthy
    deploy:
      restart_policy:
        condition: on-failure
    volumes:
      - ./backend:/app

  scraper_0:
    image: assemblage-gcc:default
    build:
      context: .
      dockerfile: docker/gcc/Dockerfile
    depends_on:
      rabbitmq:
        condition: service_healthy
    environment:
      PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION: python
      PYTHONUNBUFFERED: 1
      name: scraper_0
      TYPE: scraper
    env_file:
      - {SECRETS_FILE}
    deploy:
      resources:
        limits:
          memory: 2048M
      restart_policy:
        condition: on-failure
    volumes:
      - ./backend:/app
{builders}
  minio:
    image: minio/minio:latest
    container_name: minio
    healthcheck:
      test: ["CMD-SHELL", "curl -l -f http://127.0.0.1:9001/minio/healthcheck/live || exit 1"]
      interval: 10s
      start_period: 5s
    ports:
      - "9000:9000"
      - "9001:9001"
    env_file:
      - {SECRETS_FILE}
    volumes:
      - minio_data:/data
    command: server /data --console-address ":9001"
    restart: unless-stopped

volumes:
  db-data:
    driver: local
  rabbitmq-data:
    driver: local
  minio_data:
    driver: local
""")
    return path


# ── Compose helpers ──────────────────────────────────────────────────────────────


def find_compose() -> Path:
    for name in [COMPOSE_FILE, "docker-compose-s3.yml", "docker-compose.yml"]:
        p = PROJECT_ROOT / name
        if p.exists():
            return p
    return PROJECT_ROOT / "docker-compose.yml"


def compose_cmd(*args):
    cf = find_compose()
    return ["docker", "compose", "-f", str(cf)] + list(args)


# ── Actions ──────────────────────────────────────────────────────────────────────


def _mask(val):
    """Mask a secret value for display."""
    if not val:
        return "[dim]<not set>[/dim]"
    return val[:4] + "*" * max(0, len(val) - 4)


def _config_fields(cfg):
    """Return list of (label, kind, attr, display_value) for the config form.
    kind: 'str', 'secret', 'bool', 'int', 'action'."""
    return [
        ("GitHub Token", "secret", "github_token", _mask(cfg.github_token)),
        ("DB Password", "secret", "db_password", _mask(cfg.db_password)),
        ("S3 Username", "str", "s3_user", cfg.s3_user),
        ("S3 Password", "secret", "s3_pass", _mask(cfg.s3_pass)),
        None,  # separator
        ("GCC Enabled", "bool", "gcc_enabled", None),
        ("GCC Builders", "int", "gcc_count", None),
        ("Clang Enabled", "bool", "clang_enabled", None),
        ("Clang Builders", "int", "clang_count", None),
        None,  # separator
        ("O0 (None)", "bool", "opt_none", None),
        ("O1 (Low)", "bool", "opt_low", None),
        ("O2 (Medium)", "bool", "opt_medium", None),
        ("O3 (High)", "bool", "opt_high", None),
        None,  # separator
        ("Restart Every", "hours", "restart_interval_hours", None),
        None,  # separator
        ("Save & Generate", "action", "_save", None),
        ("Back", "action", "_back", None),
    ]


def _draw_config(cfg, fields, cur, msg=""):
    """Draw the single-page config form."""
    console.clear()
    console.print(f"[bold cyan]{LOGO}[/bold cyan]")
    console.print("  [bold]Configure Assemblage[/bold]")
    console.print("  [dim]↑↓ navigate  space/enter toggle/edit  q back[/dim]\n")
    row = 0
    for f in fields:
        if f is None:
            console.print()
            continue
        label, kind, attr, display = f
        selected = row == cur
        prefix = "[bold cyan]> " if selected else "    "
        suffix = "[/bold cyan]" if selected else ""

        if kind == "bool":
            val = getattr(cfg, attr)
            mark = "[green]on[/green]" if val else "[red]off[/red]"
            console.print(f"  {prefix}{label:<18} {mark}{suffix}")
        elif kind == "int":
            val = getattr(cfg, attr)
            console.print(f"  {prefix}{label:<18} {val}{suffix}")
        elif kind == "hours":
            val = getattr(cfg, attr)
            show = f"{val}h" if val > 0 else "[dim]off[/dim]"
            console.print(f"  {prefix}{label:<18} {show}{suffix}")
        elif kind in ("str", "secret"):
            console.print(f"  {prefix}{label:<18} {display}{suffix}")
        elif kind == "action":
            console.print(f"  {prefix}[bold]{label}[/bold]{suffix}")
        row += 1
    if msg:
        console.print(f"\n  {msg}")


def _edit_field(cfg, kind, attr):
    """Inline edit for a single field. Returns True if changed."""
    cur_val = getattr(cfg, attr)
    if kind == "bool":
        setattr(cfg, attr, not cur_val)
        return True
    elif kind == "int":
        try:
            val = input(f"  New value [{cur_val}]: ").strip()
            if val:
                setattr(cfg, attr, max(1, int(val)))
                return True
        except ValueError:
            pass
        return False
    elif kind == "hours":
        try:
            val = input(f"  Hours (0=off) [{cur_val}]: ").strip()
            if val:
                setattr(cfg, attr, max(0, int(val)))
                return True
        except ValueError:
            pass
        return False
    elif kind == "secret":
        val = getpass.getpass("  New value (enter to keep): ").strip()
        if val:
            setattr(cfg, attr, val)
            return True
        return False
    elif kind == "str":
        val = input(f"  New value [{cur_val}]: ").strip()
        if val:
            setattr(cfg, attr, val)
            return True
        return False
    return False


def do_configure():
    cfg = TUIConfig.load()
    fields = _config_fields(cfg)
    # Build index of navigable rows (skip separators)
    nav = [i for i, f in enumerate(fields) if f is not None]
    cur = 0  # index into nav
    msg = ""

    while True:
        # Rebuild display values for secrets/strings
        fields = _config_fields(cfg)
        _draw_config(cfg, fields, cur, msg)
        msg = ""

        key = _getch()
        if key == "\x1b[A":
            cur = (cur - 1) % len(nav)
        elif key == "\x1b[B":
            cur = (cur + 1) % len(nav)
        elif key in ("q", "\x03"):
            return
        elif key in ("\r", "\n", " "):
            f = fields[nav[cur]]
            label, kind, attr, display = f
            if kind == "action" and attr == "_back":
                return
            elif kind == "action" and attr == "_save":
                if not cfg.gcc_enabled and not cfg.clang_enabled:
                    msg = "[red]Need at least one compiler enabled[/red]"
                    continue
                cfg.save()
                write_secrets_env(cfg)
                write_compose_file(cfg)
                msg = (
                    "[green]Saved "
                    + CONFIG_FILE
                    + ", "
                    + SECRETS_FILE
                    + ", "
                    + COMPOSE_FILE
                    + "[/green]"
                )
                if not cfg.github_token:
                    msg += (
                        "\n  [yellow]Note: no GitHub token — scraper won't work without it[/yellow]"
                    )
            elif kind == "bool":
                setattr(cfg, attr, not getattr(cfg, attr))
            else:
                _edit_field(cfg, kind, attr)


def _compose_up():
    """Run docker compose up. Returns True on success."""
    ret = subprocess.call(
        compose_cmd("up", "--build", "-d", "--remove-orphans"), cwd=str(PROJECT_ROOT)
    )
    return ret == 0


def _compose_restart():
    """Restart all containers via down + up."""
    subprocess.call(compose_cmd("down", "--remove-orphans"), cwd=str(PROJECT_ROOT))
    return _compose_up()


def do_start():
    cfg = TUIConfig.load()
    console.clear()
    cf = find_compose()
    console.print(f"\n  [bold]Starting Assemblage[/bold]  ({cf.name})\n")

    if not _compose_up():
        console.print("\n  [red]Failed to start[/red]")
        pause()
        return

    restart_secs = cfg.restart_interval_hours * 3600 if cfg.restart_interval_hours > 0 else 0
    if restart_secs:
        console.print(
            f"\n  [green]Started. Auto-restart every {cfg.restart_interval_hours}h. Tailing logs (Ctrl+C to stop)...[/green]\n"
        )
    else:
        console.print("\n  [green]Started. Tailing logs (Ctrl+C to stop)...[/green]\n")

    metrics = {"repos": 0, "ok": 0, "fail": 0, "bins": 0}
    try:
        while True:
            cycle_start = time.time()
            proc = subprocess.Popen(
                compose_cmd("logs", "-f", "--tail", "50"),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=str(PROJECT_ROOT),
            )
            n = 0
            for line in iter(proc.stdout.readline, ""):
                s = line.rstrip()
                if not s:
                    continue
                print(s)
                _parse(s, metrics)
                n += 1
                if n % 100 == 0:
                    _print_metrics(metrics)
                if restart_secs and (time.time() - cycle_start) >= restart_secs:
                    break
            proc.terminate()
            proc.wait()

            if not restart_secs:
                break

            console.print("\n  [yellow]Auto-restarting containers...[/yellow]\n")
            if not _compose_restart():
                console.print("\n  [red]Restart failed[/red]")
                break
            console.print(
                f"\n  [green]Restarted. Next restart in {cfg.restart_interval_hours}h.[/green]\n"
            )
    except KeyboardInterrupt:
        try:
            proc.terminate()
            proc.wait()
        except Exception:
            pass

    console.print()
    _print_metrics(metrics, final=True)
    pause()


def do_stop():
    console.clear()
    console.print("\n  [bold]Stopping Assemblage[/bold]\n")
    subprocess.call(compose_cmd("down", "--remove-orphans"), cwd=str(PROJECT_ROOT))
    pause()


def do_status():
    console.clear()
    console.print("\n  [bold]Container Status[/bold]\n")
    subprocess.call(compose_cmd("ps"), cwd=str(PROJECT_ROOT))
    console.print("\n  [bold]Logs[/bold]  [dim](Ctrl+C to stop)[/dim]\n")
    try:
        proc = subprocess.Popen(
            compose_cmd("logs", "-f", "--tail", "20"),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=str(PROJECT_ROOT),
        )
        for line in iter(proc.stdout.readline, ""):
            print(line.rstrip())
    except KeyboardInterrupt:
        proc.terminate()
        proc.wait()
    pause()


def _parse(line, m):
    hit = re.search(r"Received (\d+) / saved (\d+) repos", line)
    if hit:
        m["repos"] += int(hit.group(2))
        return
    hit = re.search(r"Builds completed: (\d+) \((\d+) successes, (\d+) failures\)", line)
    if hit:
        m["ok"] = int(hit.group(2))
        m["fail"] = int(hit.group(3))
        return
    hit = re.search(r"(\d+) binaries found", line)
    if hit:
        m["bins"] += int(hit.group(1))


def _print_metrics(m, final=False):
    tag = "bold" if final else "dim"
    console.print(
        f"  [{tag}]--- Repos: {m['repos']}  |  "
        f"Builds: {m['ok']} ok / {m['fail']} fail  |  "
        f"Binaries: {m['bins']} ---[/{tag}]"
    )


# ── Main ─────────────────────────────────────────────────────────────────────────


def main():
    while True:
        cfg_ok = (PROJECT_ROOT / CONFIG_FILE).exists()
        status = "[green]configured[/green]" if cfg_ok else "[red]not configured[/red]"

        choice = select(
            f"ASSEMBLAGE  [dim]({status})[/dim]",
            [
                "Configure",
                "Start",
                "Stop",
                "Status",
                "Quit",
            ],
        )

        if choice == 0:
            do_configure()
        elif choice == 1:
            do_start()
        elif choice == 2:
            do_stop()
        elif choice == 3:
            do_status()
        else:
            console.clear()
            break


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print()
