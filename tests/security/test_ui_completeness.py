"""No visible control may be missing its handler.

These checks are the automated form of the audit required by
``docs/09-testing-acceptance.md`` (AC-025).
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

from app.bot import texts
from app.bot.keyboards.reply import MAIN_BUTTON_TEXTS

ROOT = Path(__file__).resolve().parents[2]
ROUTERS = ROOT / "app" / "bot" / "routers"

REQUIRED_COMMANDS = {
    "start",
    "menu",
    "cancel",
    "help",
    "privacy",
    "status",
    "admin",
    "invite",
}


def router_sources() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in ROUTERS.rglob("*.py"))


def test_every_reply_keyboard_button_has_a_handler() -> None:
    sources = router_sources()
    labels = {name: getattr(texts, name) for name in dir(texts) if name.startswith("BTN_")}
    handled = {
        labels[constant]
        for constant in re.findall(r"F\.text == texts\.(\w+)", sources)
        if constant in labels
    }
    assert MAIN_BUTTON_TEXTS - handled == set()


def test_every_documented_command_has_a_handler() -> None:
    sources = router_sources()
    handled = set(re.findall(r'Command\("(\w+)"\)', sources))
    handled |= set(re.findall(r'F\.text\.startswith\("/(\w+)"\)', sources))
    if "CommandStart" in sources:
        handled.add("start")
    assert REQUIRED_COMMANDS - handled == set()


def test_every_callback_payload_has_a_handler() -> None:
    """Runs the button audit used by the Makefile and by CI."""
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "handler_audit.py")],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_no_screen_is_a_dead_end() -> None:
    """Every keyboard builder call must offer a way back or to the menu."""
    offenders: list[str] = []
    for path in ROUTERS.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else (node.func.id if isinstance(node.func, ast.Name) else None)
            )
            if name != "build_actions":
                continue
            keywords = {keyword.arg for keyword in node.keywords}
            shows_menu = True
            for keyword in node.keywords:
                if keyword.arg == "show_menu" and isinstance(keyword.value, ast.Constant):
                    shows_menu = bool(keyword.value.value)
            if not shows_menu and "back_target" not in keywords:
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    # The consent screen is the only intentional exception: the user is not
    # active yet, so there is no menu to go back to.
    assert len(offenders) <= 1, offenders


def test_the_italian_texts_carry_the_meaning_without_emoji() -> None:
    """Accessibility: no message may rely on an emoji alone."""
    emoji_only = [
        name
        for name in dir(texts)
        if name.isupper()
        and isinstance(getattr(texts, name), str)
        and getattr(texts, name).strip()
        and not any(character.isalpha() for character in getattr(texts, name))
    ]
    assert emoji_only == []


def test_no_configuration_value_is_declared_but_unused() -> None:
    """Dead configuration is a placeholder in disguise."""
    from app.config import Settings

    # These are consumed through a derived property or by the Compose stack.
    indirect = {
        "app_blind_index_key",
        "app_previous_encryption_keys",
        "app_secret_key",
        "database_host",
        "database_port",
        "mysql_database",
        "mysql_password",
        "mysql_user",
        "telegram_admin_ids",
        # Consumed by Settings.invite_link, which the administration screens call.
        "telegram_bot_username",
    }
    sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "app").rglob("*.py")
        if path.name != "config.py"
    )
    unused = sorted(
        name for name in Settings.model_fields if name not in indirect and name not in sources
    )
    assert unused == []


def test_every_documented_variable_exists_in_the_settings() -> None:
    """`.env.example` must not advertise a variable the application ignores."""
    from app.config import Settings

    documented = {
        line.split("=", 1)[0].strip().lower()
        for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#") and "=" in line
    }
    known = set(Settings.model_fields) | {
        # Consumed by the Compose stack rather than by the application.
        "mysql_root_password",
        "backup_retention_days",
        "backup_passphrase",
        "backup_passphrase_file",
        # Select the published image to run; never reach the process.
        "opbg_image",
        "opbg_tag",
    }
    assert documented - known == set()


def test_the_repository_guardrails_pass() -> None:
    """The scanners that gate CI must also pass as part of the suite."""
    for script in ("secret_scan.sh", "placeholder_scan.sh"):
        result = subprocess.run(
            ["bash", str(ROOT / "scripts" / script)],
            capture_output=True,
            text=True,
            cwd=ROOT,
            check=False,
        )
        assert result.returncode == 0, f"{script}:\n{result.stdout}\n{result.stderr}"


def test_the_scanners_never_scan_themselves() -> None:
    """A scanner that matches its own patterns would fail on every run."""
    for script in ("secret_scan.sh", "placeholder_scan.sh"):
        source = (ROOT / "scripts" / script).read_text(encoding="utf-8")
        assert "':!scripts/secret_scan.sh'" in source, script
        assert "':!scripts/placeholder_scan.sh'" in source, script
