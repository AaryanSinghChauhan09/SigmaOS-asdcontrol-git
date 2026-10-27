"""Git-tracked ASD configuration for SigmaOS-asdcontrol-git (inspired by
asdcontrol-git).

Principles: the key schema is the single source of truth (typed, ranged,
fail-fast on unknown keys), config writes are atomic and deterministic, plan and
execute are separated (`sync_plan` only builds git argv -- self_test never runs
git), git porcelain output is parsed by a pure function.
"""
import argparse, json, os, re, shlex, subprocess, sys

ENGINE = {
    "name": "sigma-asd",
    "summary": "Git-tracked ASD config: typed get/set, sync plan, status",
    "language": "python",
    "verbs": ["get", "set", "sync-plan", "status"],
    "principles": ["Separation of Concerns", "Defensive Programming",
                   "Fail-Fast", "KISS", "DRY", "Deterministic Testing"],
}

cli_doc = """\
$ sigma-asd get brightness --config asd.conf
80
$ sigma-asd set mode night --config asd.conf
mode=night
$ sigma-asd sync-plan --repo ~/dotfiles --message "asd: night mode"
$ sigma-asd status --config asd.conf --repo ~/dotfiles
"""

# key -> spec (type, default, and constraints)
KEYS = {
    "enabled":     {"type": "bool",  "default": True},
    "brightness":  {"type": "int",   "min": 0,   "max": 100,  "default": 80},
    "gamma":       {"type": "float", "min": 0.3, "max": 3.0,  "default": 1.0},
    "temperature": {"type": "int",   "min": 3000, "max": 9000, "default": 6500},
    "mode":        {"type": "enum",  "choices": ("auto", "manual", "night"),
                    "default": "auto"},
    "device":      {"type": "str",   "default": "eDP-1"},
}
_HEADER = "# sigma-asd configuration -- managed by sigma-asdcontrol"
_BOOL_WORDS = {"true": True, "on": True, "1": True, "yes": True,
               "false": False, "off": False, "0": False, "no": False}
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")


class AsdError(ValueError):
    pass


def validate(key, value):
    """Coerce `value` to the schema type for `key`. Fail-fast otherwise."""
    if key not in KEYS:
        raise AsdError(f"unknown key {key!r} (have: {', '.join(KEYS)})")
    spec = KEYS[key]
    kind = spec["type"]
    if kind == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in _BOOL_WORDS:
            return _BOOL_WORDS[value.strip().lower()]
        raise AsdError(f"{key} wants a boolean (true/false), got {value!r}")
    if kind == "int":
        if isinstance(value, bool):
            raise AsdError(f"{key} wants an integer, got {value!r}")
        if isinstance(value, str):
            try:
                value = int(value.strip(), 10)
            except ValueError as e:
                raise AsdError(f"{key} wants an integer, got {value!r}") from e
        if not isinstance(value, int):
            raise AsdError(f"{key} wants an integer, got {value!r}")
        if not spec["min"] <= value <= spec["max"]:
            raise AsdError(f"{key} must be {spec['min']}..{spec['max']}, "
                           f"got {value}")
        return value
    if kind == "float":
        if isinstance(value, bool):
            raise AsdError(f"{key} wants a number, got {value!r}")
        if isinstance(value, str):
            try:
                value = float(value.strip())
            except ValueError as e:
                raise AsdError(f"{key} wants a number, got {value!r}") from e
        if not isinstance(value, (int, float)):
            raise AsdError(f"{key} wants a number, got {value!r}")
        value = float(value)
        if not spec["min"] <= value <= spec["max"]:
            raise AsdError(f"{key} must be {spec['min']}..{spec['max']}, "
                           f"got {value}")
        return value
    if kind == "enum":
        if not isinstance(value, str) or value.strip() not in spec["choices"]:
            raise AsdError(f"{key} must be one of {spec['choices']}, "
                           f"got {value!r}")
        return value.strip()
    # plain string
    if not isinstance(value, str) or not value.strip():
        raise AsdError(f"{key} wants a non-empty string, got {value!r}")
    value = value.strip()
    if "\n" in value or "\x00" in value:
        raise AsdError(f"{key} must be a single line")
    return value


def read_config(path):
    """Typed config dict (schema order, defaults for absent keys)."""
    values = {}
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                if "=" not in s:
                    raise AsdError(f"{path}:{lineno}: expected key=value, "
                                   f"got {s!r}")
                k, _, raw = s.partition("=")
                k = k.strip()
                if k not in KEYS:
                    raise AsdError(f"{path}:{lineno}: unknown key {k!r}")
                if k in values:
                    raise AsdError(f"{path}:{lineno}: duplicate key {k!r}")
                values[k] = validate(k, raw)
    return {k: values.get(k, KEYS[k]["default"]) for k in KEYS}


def _fmt(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def write_config(path, cfg):
    """Atomic, deterministic rewrite (schema order)."""
    lines = [_HEADER]
    for k in KEYS:
        lines.append(f"{k}={_fmt(cfg.get(k, KEYS[k]['default']))}")
    tmp = f"{path}.sigma-tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    os.replace(tmp, path)


def get_value(path, key):
    if key not in KEYS:
        raise AsdError(f"unknown key {key!r} (have: {', '.join(KEYS)})")
    return read_config(path)[key]


def set_value(path, key, value):
    typed = validate(key, value)
    cfg = read_config(path)
    cfg[key] = typed
    write_config(path, cfg)
    return cfg


# --------------------------------------------------------------------------- #
#  Git plans (pure) -- self_test never runs git
# --------------------------------------------------------------------------- #
def sync_plan(repo, message, remote="origin", branch="main"):
    for what, v in (("repo", repo), ("message", message), ("remote", remote),
                    ("branch", branch)):
        if not isinstance(v, str) or not v.strip():
            raise AsdError(f"empty {what}")
        if "\n" in v or "\x00" in v:
            raise AsdError(f"{what} must be a single line")
        if what in ("repo", "remote", "branch") and any(c.isspace() for c in v):
            raise AsdError(f"{what} must not contain whitespace: {v!r}")
    return {"repo": repo, "steps": [
        {"argv": ["git", "-C", repo, "add", "-A"]},
        {"argv": ["git", "-C", repo, "commit", "-m", message]},
        {"argv": ["git", "-C", repo, "push", remote, branch]},
    ]}


def status_plan(repo):
    if not isinstance(repo, str) or not repo.strip():
        raise AsdError("empty repo")
    return {"argv": ["git", "-C", repo, "status", "--porcelain"]}


def parse_status(porcelain):
    """Pure parser for `git status --porcelain` output."""
    out = []
    for line in porcelain.splitlines():
        if not line.strip():
            continue
        if len(line) < 4 or line[2] != " ":
            raise AsdError(f"bad porcelain line: {line!r}")
        out.append({"code": line[:2], "path": line[3:]})
    return out


def execute(steps):
    """THIN side-effecting layer: run planned git steps. Never self-tested."""
    result = None
    for step in steps:
        result = subprocess.run(step["argv"], capture_output=True, text=True)
        if result.returncode != 0:
            break
    return result


# --------------------------------------------------------------------------- #
#  CLI
# --------------------------------------------------------------------------- #
DEFAULT_CONFIG = os.path.expanduser("~/.config/sigma-asd/asd.conf")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=ENGINE["name"], description=ENGINE["summary"])
    p.add_argument("--config", default=DEFAULT_CONFIG)
    sub = p.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("get", help="read a typed value")
    g.add_argument("key", choices=sorted(KEYS))
    s = sub.add_parser("set", help="validate and write a value")
    s.add_argument("key", choices=sorted(KEYS))
    s.add_argument("value")
    sp = sub.add_parser("sync-plan", help="build git add/commit/push argv")
    sp.add_argument("--repo", required=True)
    sp.add_argument("--message", required=True)
    sp.add_argument("--remote", default="origin")
    sp.add_argument("--branch", default="main")
    st = sub.add_parser("status", help="print config + git porcelain status")
    st.add_argument("--repo", default=None)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "get":
            print(_fmt(get_value(args.config, args.key)))
        elif args.cmd == "set":
            cfg = set_value(args.config, args.key, args.value)
            print(f"{args.key}={_fmt(cfg[args.key])}")
        elif args.cmd == "sync-plan":
            for step in sync_plan(args.repo, args.message, args.remote,
                                  args.branch)["steps"]:
                print(shlex.join(step["argv"]))
        elif args.cmd == "status":
            cfg = read_config(args.config)
            for k in KEYS:
                print(f"{k}={_fmt(cfg[k])}")
            if args.repo:
                out = subprocess.run(status_plan(args.repo)["argv"],
                                     capture_output=True, text=True)
                if out.returncode != 0:
                    print(f"error: git status failed: {out.stderr.strip()}",
                          file=sys.stderr)
                    return 1
                rows = parse_status(out.stdout)
                print(f"-- git status ({len(rows)} change(s)) --")
                for r in rows:
                    print(f"{r['code']} {r['path']}")
        return 0
    except (AsdError, OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


def self_test() -> None:
    import tempfile

    # --- typed validation ---------------------------------------------------
    assert validate("brightness", "90") == 90
    assert validate("brightness", 90) == 90
    assert validate("enabled", "off") is False
    assert validate("enabled", True) is True
    assert validate("gamma", "1.75") == 1.75
    assert validate("mode", "night") == "night"
    assert validate("device", " HDMI-A-1 ") == "HDMI-A-1"
    for bad_call in ((validate, ("nope", "1")),          # unknown key
                     (validate, ("brightness", 101)),    # range
                     (validate, ("brightness", -1)),
                     (validate, ("brightness", "90.5")),
                     (validate, ("brightness", True)),   # bool is not an int here
                     (validate, ("gamma", "abc")),
                     (validate, ("gamma", 9.9)),
                     (validate, ("mode", "turbo")),
                     (validate, ("enabled", "maybe")),
                     (validate, ("device", "")),
                     (validate, ("device", "a\nb")),
                     (validate, ("temperature", "6500K"))):
        try:
            bad_call[0](*bad_call[1])
        except AsdError:
            pass
        else:
            raise AssertionError(f"should have rejected: {bad_call}")

    # --- config round-trip (tempfile only) ----------------------------------
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "asd.conf")
        cfg = read_config(path)                      # missing file -> defaults
        assert cfg["brightness"] == 80 and cfg["mode"] == "auto"
        assert cfg["enabled"] is True and cfg["gamma"] == 1.0
        assert get_value(path, "temperature") == 6500

        set_value(path, "brightness", "92")
        set_value(path, "mode", "night")
        set_value(path, "enabled", "false")
        assert get_value(path, "brightness") == 92
        assert get_value(path, "mode") == "night"
        assert get_value(path, "enabled") is False
        with open(path, "r", encoding="utf-8") as fh:
            body = fh.read()
        assert body.startswith("# sigma-asd configuration")
        assert "brightness=92" in body and "mode=night" in body
        assert body.splitlines()[1].startswith("enabled=")  # schema order

        # deterministic rewrite
        set_value(path, "brightness", "92")
        with open(path, "r", encoding="utf-8") as fh:
            same = fh.read()
        assert same == body

        # invalid value leaves the file untouched
        try:
            set_value(path, "brightness", "500")
        except AsdError:
            pass
        else:
            raise AssertionError("out-of-range set not rejected")
        assert get_value(path, "brightness") == 92

        # corrupt files fail fast
        for content in ("brightness\n", "nosuchkey=1\n",
                        "mode=auto\nmode=night\n", "brightness=abc\n"):
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(content)
            try:
                read_config(path)
            except AsdError:
                pass
            else:
                raise AssertionError(f"config should have been rejected: {content!r}")

    # --- git plans (pure argv, no git) --------------------------------------
    plan = sync_plan("~/dotfiles", "asd: night mode")
    assert [s["argv"] for s in plan["steps"]] == [
        ["git", "-C", "~/dotfiles", "add", "-A"],
        ["git", "-C", "~/dotfiles", "commit", "-m", "asd: night mode"],
        ["git", "-C", "~/dotfiles", "push", "origin", "main"],
    ]
    plan = sync_plan("/repos/d", "msg", remote="upstream", branch="wip")
    assert plan["steps"][2]["argv"][-2:] == ["upstream", "wip"]
    assert status_plan("/repos/d")["argv"] == \
        ["git", "-C", "/repos/d", "status", "--porcelain"]
    for bad_call in ((sync_plan, ("", "msg")),
                     (sync_plan, ("/r", "")),
                     (sync_plan, ("/r", "two\nlines")),
                     (sync_plan, ("/r", "msg", "origin main")),
                     (sync_plan, ("/r", "msg", "a b", "main")),
                     (status_plan, ("  ",))):
        try:
            bad_call[0](*bad_call[1])
        except AsdError:
            pass
        else:
            raise AssertionError(f"should have rejected: {bad_call}")
    assert sync_plan("/r", "m") == sync_plan("/r", "m")  # deterministic

    # --- porcelain parsing (pure) -------------------------------------------
    rows = parse_status(" M asd.conf\n?? presets/night.json\nA  new.txt\n\n")
    assert [(r["code"], r["path"]) for r in rows] == [
        (" M", "asd.conf"), ("??", "presets/night.json"), ("A ", "new.txt")]
    assert parse_status("") == []
    for bad in ("garbage", "M", "M x"):
        try:
            parse_status(bad)
        except AsdError:
            pass
        else:
            raise AssertionError(f"porcelain should have been rejected: {bad!r}")

    # --- defaults are themselves valid --------------------------------------
    for k, spec in KEYS.items():
        assert validate(k, spec["default"]) == spec["default"], k


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
