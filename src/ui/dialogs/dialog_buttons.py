from collections.abc import Callable, Iterable
from types import MappingProxyType

from PyQt6.QtWidgets import QDialog, QDialogButtonBox


DialogButton = str | QDialogButtonBox.StandardButton

_BUTTON_ALIASES = MappingProxyType({
    # Accept-like
    "ok": QDialogButtonBox.StandardButton.Ok,
    "okay": QDialogButtonBox.StandardButton.Ok,
    "accept": QDialogButtonBox.StandardButton.Ok,
    "confirm": QDialogButtonBox.StandardButton.Ok,
    "proceed": QDialogButtonBox.StandardButton.Ok,
    "yes": QDialogButtonBox.StandardButton.Yes,
    "save": QDialogButtonBox.StandardButton.Save,
    "done": QDialogButtonBox.StandardButton.Ok,
    # Reject/close-like
    "cancel": QDialogButtonBox.StandardButton.Cancel,
    "decline": QDialogButtonBox.StandardButton.Cancel,
    "reject": QDialogButtonBox.StandardButton.Cancel,
    "no": QDialogButtonBox.StandardButton.No,
    "close": QDialogButtonBox.StandardButton.Close,
    "dismiss": QDialogButtonBox.StandardButton.Close,
})

_DEFAULT_BUTTONS: tuple[DialogButton, ...] = ("ok", "cancel")


def _normalize_button_name(name: str) -> str:
    return name.strip().lower().translate(str.maketrans("", "", "-_ "))


_REJECT_BUTTONS = frozenset({
    QDialogButtonBox.StandardButton.Cancel,
    QDialogButtonBox.StandardButton.No,
    QDialogButtonBox.StandardButton.Close,
})


def _resolve_button(button: DialogButton) -> QDialogButtonBox.StandardButton:
    if isinstance(button, QDialogButtonBox.StandardButton):
        return button

    key = _normalize_button_name(button)
    if key not in _BUTTON_ALIASES:
        supported = ", ".join(sorted(_BUTTON_ALIASES.keys()))
        raise ValueError(
            f"Unsupported dialog button alias: {button!r}. Supported aliases: {supported}"
        )
    return _BUTTON_ALIASES[key]


def create_standard_dialog_buttons(
    dialog: QDialog,
    buttons: Iterable[DialogButton] = _DEFAULT_BUTTONS,
    *,
    center_buttons: bool = False,
    on_accept: Callable[[], None] | None = None,
    on_reject: Callable[[], None] | None = None,
) -> QDialogButtonBox:
    """Create a QDialogButtonBox using normalized button aliases.

    All aliases normalize to canonical outcomes: Ok, Cancel, Yes, No, or Close.
    """
    input_buttons = tuple(buttons)
    if not input_buttons:
        raise ValueError("At least one dialog button is required")

    resolved_buttons = tuple(_resolve_button(button) for button in input_buttons)
    has_reject_button = any(button in _REJECT_BUTTONS for button in resolved_buttons)

    if on_reject and not has_reject_button:
        raise ValueError("on_reject provided but no reject button was specified")

    standard_mask = QDialogButtonBox.StandardButton.NoButton
    for button in resolved_buttons:
        standard_mask |= button

    button_box = QDialogButtonBox(standard_mask, dialog)
    button_box.setCenterButtons(center_buttons)

    button_box.accepted.connect(on_accept or dialog.accept)
    if has_reject_button:
        button_box.rejected.connect(on_reject or dialog.reject)
    return button_box
