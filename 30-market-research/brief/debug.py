"""Debug output for the suite (`--debug`): the full traceback of a crash, on stderr only.

Without it a crash is one evidence line, `infrastructure: TypeError: <message>`, with no file or
line, and finding it means rerunning the task by hand. With it the traceback goes to stderr; the
evidence line and results.json stay exactly as without it.

Tokens never reach a traceback: the client keeps the token in a request header and its errors
name settings, never their values (bug hunt E1). Locals are not printed.
"""
import sys
import traceback

ENABLED = False
current = ""          # the task running now (set by suite.py), to label a traceback a task catches itself


def crash(error, where=""):
    """Print `error`'s traceback to stderr when debug is on; otherwise do nothing."""
    if not ENABLED or not isinstance(error, BaseException):
        return
    where = where or current
    label = f" in {where}" if where else ""
    print(f"debug: traceback{label}:", file=sys.stderr)
    traceback.print_exception(type(error), error, error.__traceback__, file=sys.stderr)


def step(text):
    """One progress line on stderr when debug is on, so a traceback can be told apart by task."""
    if ENABLED:
        print(f"debug: {text}", file=sys.stderr, flush=True)


VALUE_MAX = 300      # characters of one value in a want/got line


def diff(deal_id, what, want, got):
    """'debug: <task> <deal> <what>: want <value> | got <value>' when debug is on. The values are
    customer data, so they go to stderr only, never into evidence or results.json."""
    if ENABLED:
        where = f"{current} " if current else ""
        print(f"debug: {where}{_short(deal_id)} {what}: want {_value(want)} | got {_value(got)}",
              file=sys.stderr, flush=True)


def _short(value):
    """An id cut to 8 characters; for 'Entity:id' only the id part is cut."""
    entity, sep, ident = str(value).rpartition(":")
    return f"{entity}{sep}{ident[:8]}"


def _value(value):
    from .values import display_text
    import json
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        text = repr(value)
    text = " ".join(display_text(text).split())
    return text if len(text) <= VALUE_MAX else text[:VALUE_MAX - 1] + "…"
