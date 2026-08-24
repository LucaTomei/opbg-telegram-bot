"""Verify that every inline button has a registered handler.

Buttons are built through :mod:`app.bot.callbacks`, so the audit walks the bot
package, resolves every ``callbacks.build(namespace, action, ...)`` call and
every handler filter, and fails when a payload has no matching handler.
"""

from __future__ import annotations

import ast
import re
import sys
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOT_PACKAGE = ROOT / "app" / "bot"
CALLBACKS_MODULE = BOT_PACKAGE / "callbacks.py"

#: Payloads produced by helper functions in ``app.bot.callbacks``.
HELPER_PAYLOADS: dict[str, str] = {
    "menu": "nv|menu",
    "cancel": "nv|cancel",
    "noop": "nv|noop",
}


def namespace_constants() -> dict[str, str]:
    """Read the namespace constants declared in ``app.bot.callbacks``."""
    source = CALLBACKS_MODULE.read_text(encoding="utf-8")
    return {
        match.group(1): match.group(2)
        for match in re.finditer(r'^(\w+): Final\[str\] = "([^"]+)"$', source, re.MULTILINE)
    }


def module_constants(tree: ast.Module, namespaces: dict[str, str]) -> dict[str, str]:
    """Resolve module level string constants, including callback aliases."""
    constants: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        value = node.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            constants[target.id] = value.value
        elif isinstance(value, ast.Attribute) and value.attr in namespaces:
            constants[target.id] = namespaces[value.attr]
    return constants


def resolve(node: ast.expr, constants: dict[str, str]) -> str | None:
    """Resolve a literal, a named constant or an f-string of constants."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    if isinstance(node, ast.Attribute):
        return constants.get(node.attr)
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            elif isinstance(value, ast.FormattedValue):
                resolved = resolve(value.value, constants)
                if resolved is None:
                    return None
                parts.append(resolved)
            else:
                return None
        return "".join(parts)
    return None


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    if isinstance(node.func, ast.Name):
        return node.func.id
    return None


def iter_built_payloads(tree: ast.Module, constants: dict[str, str]) -> Iterator[str]:
    """Yield every ``namespace|action`` payload built in this module."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        if name in HELPER_PAYLOADS and not node.args:
            yield HELPER_PAYLOADS[name]
            continue
        if name != "build" or len(node.args) < 2:
            continue
        namespace = resolve(node.args[0], constants)
        action = resolve(node.args[1], constants)
        if namespace and action:
            yield f"{namespace}|{action}"


def iter_handler_filters(tree: ast.Module, constants: dict[str, str]) -> Iterator[str]:
    """Yield every callback prefix a handler in this module reacts to."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            for argument in decorator.args:
                yield from _filter_prefixes(argument, constants)


def _filter_prefixes(node: ast.expr, constants: dict[str, str]) -> Iterator[str]:
    if isinstance(node, ast.Call) and _call_name(node) == "startswith" and node.args:
        resolved = resolve(node.args[0], constants)
        if resolved:
            yield resolved
        return
    if isinstance(node, ast.Compare) and len(node.ops) == 1:
        for candidate in (node.left, *node.comparators):
            if isinstance(candidate, ast.Call) and _call_name(candidate) == "build":
                if len(candidate.args) >= 2:
                    namespace = resolve(candidate.args[0], constants)
                    action = resolve(candidate.args[1], constants)
                    if namespace and action:
                        yield f"{namespace}|{action}"
            elif isinstance(candidate, ast.Call) and _call_name(candidate) in HELPER_PAYLOADS:
                yield HELPER_PAYLOADS[_call_name(candidate) or ""]


def main() -> int:
    namespaces = namespace_constants()
    used: dict[str, set[str]] = {}
    handled: set[str] = set()

    for path in sorted(BOT_PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        constants = module_constants(tree, namespaces)
        constants.update(namespaces)
        for payload in iter_built_payloads(tree, constants):
            used.setdefault(payload, set()).add(str(path.relative_to(ROOT)))
        handled.update(iter_handler_filters(tree, constants))

    failures = [
        f"{sorted(sources)[0]}: no handler for callback '{payload}'"
        for payload, sources in sorted(used.items())
        if not any(payload.startswith(prefix) for prefix in handled)
    ]

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)  # noqa: T201
        return 1
    print(  # noqa: T201
        f"handler audit: {len(used)} distinct callback payloads, all handled"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
