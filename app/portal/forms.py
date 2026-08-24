"""HTML form extraction helpers.

The portal is a server rendered Laravel application: every mutating step posts
a form that carries hidden fields and a fresh CSRF token.  Values are always
copied from the document instead of being hardcoded, so a change in the portal
surfaces as a contract error rather than as a silently wrong request.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from selectolax.parser import HTMLParser, Node

from app.domain.errors import PortalContractChanged

CSRF_FIELD = "_token"


@dataclass(frozen=True, slots=True)
class FormOption:
    """One ``<option>`` of a ``<select>`` element."""

    value: str
    label: str
    selected: bool = False
    disabled: bool = False


@dataclass(slots=True)
class HtmlForm:
    """A parsed HTML form with its default field values."""

    action: str
    method: str
    fields: dict[str, str] = field(default_factory=dict)
    options: dict[str, tuple[FormOption, ...]] = field(default_factory=dict)
    has_submit: bool = False

    def payload(self, overrides: Mapping[str, str] | None = None) -> dict[str, str]:
        """Return the form payload with ``overrides`` applied on top of defaults."""
        data = dict(self.fields)
        data.update(overrides or {})
        return data

    def require(self, *names: str) -> None:
        """Fail with a contract error when an expected field disappeared."""
        missing = [name for name in names if name not in self.fields]
        if missing:
            raise PortalContractChanged(
                "expected form fields are missing", fields=",".join(sorted(missing))
            )


def parse_document(html: str) -> HTMLParser:
    return HTMLParser(html)


def extract_csrf_token(document: HTMLParser) -> str:
    """Read the CSRF token from a hidden input or from the ``csrf-token`` meta."""
    node = document.css_first(f'input[name="{CSRF_FIELD}"]')
    if node is not None:
        value = node.attributes.get("value")
        if value:
            return value
    meta = document.css_first('meta[name="csrf-token"]')
    if meta is not None:
        value = meta.attributes.get("content")
        if value:
            return value
    raise PortalContractChanged("no CSRF token found in the document")


def find_csrf_token(document: HTMLParser) -> str | None:
    """Like :func:`extract_csrf_token` but tolerant of documents without a token."""
    try:
        return extract_csrf_token(document)
    except PortalContractChanged:
        return None


def _iter_form_nodes(document: HTMLParser) -> Iterable[Node]:
    return document.css("form")


def _node_matches(node: Node, *, action_contains: str | None, selector_id: str | None) -> bool:
    if selector_id is not None and node.attributes.get("id") == selector_id:
        return True
    if action_contains is not None:
        action = node.attributes.get("action") or ""
        return action_contains in action
    return False


def _collect_fields(
    form_node: Node,
) -> tuple[dict[str, str], dict[str, tuple[FormOption, ...]], bool]:
    fields: dict[str, str] = {}
    options: dict[str, tuple[FormOption, ...]] = {}
    has_submit = False

    for element in form_node.css("input"):
        name = element.attributes.get("name")
        input_type = (element.attributes.get("type") or "text").lower()
        if input_type in {"submit", "button", "image"}:
            has_submit = True
            if name and element.attributes.get("value"):
                fields.setdefault(name, element.attributes["value"] or "")
            continue
        if not name:
            continue
        if input_type in {"checkbox", "radio"}:
            if "checked" in element.attributes:
                fields[name] = element.attributes.get("value") or "1"
            else:
                fields.setdefault(name, fields.get(name, ""))
            continue
        fields[name] = element.attributes.get("value") or ""

    for element in form_node.css("textarea"):
        name = element.attributes.get("name")
        if name:
            fields[name] = element.text(strip=True)

    for element in form_node.css("select"):
        name = element.attributes.get("name")
        if not name:
            continue
        collected: list[FormOption] = []
        selected_value = ""
        for option in element.css("option"):
            value = option.attributes.get("value") or ""
            label = option.text(strip=True)
            is_selected = "selected" in option.attributes
            collected.append(
                FormOption(
                    value=value,
                    label=label,
                    selected=is_selected,
                    disabled="disabled" in option.attributes,
                )
            )
            if is_selected and value:
                selected_value = value
        options[name] = tuple(collected)
        fields[name] = selected_value

    if form_node.css_first("button[type=submit]") is not None:
        has_submit = True

    return fields, options, has_submit


def extract_form(
    document: HTMLParser,
    *,
    action_contains: str | None = None,
    selector_id: str | None = None,
) -> HtmlForm:
    """Extract the first form matching ``selector_id`` or ``action_contains``."""
    for node in _iter_form_nodes(document):
        if action_contains is None and selector_id is None:
            matched = True
        else:
            matched = _node_matches(node, action_contains=action_contains, selector_id=selector_id)
        if not matched:
            continue
        fields, options, has_submit = _collect_fields(node)
        return HtmlForm(
            action=node.attributes.get("action") or "",
            method=(node.attributes.get("method") or "post").lower(),
            fields=fields,
            options=options,
            has_submit=has_submit,
        )
    raise PortalContractChanged(
        "no matching form found",
        selector=selector_id or "",
        action=action_contains or "",
    )


def find_form(
    document: HTMLParser,
    *,
    action_contains: str | None = None,
    selector_id: str | None = None,
) -> HtmlForm | None:
    try:
        return extract_form(document, action_contains=action_contains, selector_id=selector_id)
    except PortalContractChanged:
        return None
