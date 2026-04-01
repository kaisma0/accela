from PyQt6.QtGui import QColor, QFont, QFontDatabase, QPalette
from utils.paths import Paths
from pathlib import Path
import logging


def normal_palette_colors(background_color, accent_color):
    return {
        QPalette.ColorRole.Window: background_color,
        QPalette.ColorRole.WindowText: accent_color,
        QPalette.ColorRole.Base: background_color.darker(120),
        QPalette.ColorRole.AlternateBase: background_color,
        QPalette.ColorRole.ToolTipBase: accent_color,
        QPalette.ColorRole.ToolTipText: background_color,
        QPalette.ColorRole.Text: accent_color,
        QPalette.ColorRole.Button: background_color,
        QPalette.ColorRole.ButtonText: accent_color,
        QPalette.ColorRole.BrightText: accent_color.lighter(120),
        QPalette.ColorRole.Link: accent_color.lighter(120),
        QPalette.ColorRole.Highlight: accent_color,
        QPalette.ColorRole.HighlightedText: background_color,
        QPalette.ColorRole.PlaceholderText: accent_color.darker(120),
    }

def disabled_palette_colors(disabled_bg, disabled_text, background_color):
    return {
        QPalette.ColorRole.Button: disabled_bg,
        QPalette.ColorRole.ButtonText: disabled_text,
        QPalette.ColorRole.Text: disabled_text,
        QPalette.ColorRole.WindowText: disabled_text,
        QPalette.ColorRole.Base: background_color.darker(140),
    }

def apply_palette(app, accent, background):
    app.setStyle("Fusion")
    dark_palette = QPalette()

    background_color = QColor(background)
    accent_color = QColor(accent)

    disabled_bg = background_color.darker(200)
    disabled_text = QColor(100, 100, 100)

    for role, color in normal_palette_colors(background_color, accent_color).items():
        dark_palette.setColor(role, color)
    for role, color in disabled_palette_colors(disabled_bg, disabled_text, background_color).items():
        dark_palette.setColor(QPalette.ColorGroup.Disabled, role, color)

    app.setPalette(dark_palette)

    bg_hex = background_color.name()
    bg_dark_120_hex = background_color.darker(120).name()
    bg_light_120_hex = background_color.lighter(120).name()

    acc_hex = accent_color.name()
    acc_light_120_hex = accent_color.lighter(120).name()

    dis_bg_hex = disabled_bg.name()
    dis_text_hex = disabled_text.name()

    hover_lightness = 120
    selected_lightness = 150
    checked_lightness = 200
    doubled_lightness = 250

    background_color_effect = background_color
    if background_color_effect.name() == "#000000":
        background_color_effect = QColor("#282828")

    bg_eff_hover = background_color_effect.lighter(hover_lightness).name()
    bg_eff_sel = background_color_effect.lighter(selected_lightness).name()
    bg_eff_chk = background_color_effect.lighter(checked_lightness).name()
    bg_eff_dbl = background_color_effect.lighter(doubled_lightness).name()

    gradient_border = f"""
            border-top: 2px solid {acc_light_120_hex};
            border-bottom: 2px solid {acc_light_120_hex};
            border-left: 2px solid qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {acc_light_120_hex}, stop:0.5 {bg_light_120_hex}, stop:1 {acc_light_120_hex});
            border-right: 2px solid qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {acc_light_120_hex}, stop:0.5 {bg_light_120_hex}, stop:1 {acc_light_120_hex});
    """
    gradient_border_full = f"""
            border-top: 2px solid {acc_light_120_hex};
            border-bottom: 2px solid {acc_light_120_hex};
            border-left: 2px solid {acc_light_120_hex};
            border-right: 2px solid {acc_light_120_hex};
    """

    app.setStyleSheet(f"""
        QLineEdit {{
            background-color: {bg_hex};
            color: {acc_hex};
            border: 1px solid {acc_hex};
            padding: 8px;
        }}

        QLineEdit:hover {{
            background-color: {bg_hex};
            color: {acc_hex};
        }}

        QCheckBox {{
            background-color: {bg_hex};
            color: {acc_hex};
            padding: 8px;
            spacing: 8px;
        }}

        QCheckBox::indicator {{
            width: 12px;
            height: 12px;
            background: {bg_hex};
            {gradient_border}
        }}

        QCheckBox::indicator:checked {{
            background: {acc_hex};
        }}

        QCheckBox::indicator:hover {{
            {gradient_border_full}
        }}

        QDialog {{
            background-color: {bg_hex};
            color: {acc_hex};
        }}

        QListWidget {{
            background-color: {bg_dark_120_hex};
            color: {acc_hex};
            border-radius: 4px;
            /* VVV REMOVES THE WEIRD LITTLE TEXT BORDER/BACKGROUND IN DEPOT SELECTION VVV */
            outline: 0;
            border: none;
        }}

        QListWidget::item {{
            background-color: {bg_dark_120_hex};
            color: {acc_hex};
            border-radius: 4px;
            padding: 6px;
        }}

        QListWidget::item:hover {{
            background-color: {bg_eff_hover};
            color: {acc_hex};
        }}

        QListWidget::item:selected {{
            background-color: {bg_eff_sel};
            color: {acc_hex};
        }}

        QListWidget::item:checked {{
            background-color: {bg_eff_chk};
            color: {acc_hex};
            font-weight: bold;
        }}

        QListWidget::item:checked:selected {{
            background-color: {bg_eff_dbl};
            color: {acc_hex};
        }}

        QListWidget::indicator {{
            {gradient_border}
            border-radius: 4px;
        }}

        QListWidget::indicator:unchecked {{
            background-color: {bg_hex};
        }}

        QListWidget::indicator:checked {{
            background-color: {acc_hex};
        }}

        QListWidget::indicator:hover {{
            {gradient_border_full}
        }}

        QPushButton {{
            background-color: {bg_hex};
            color: {acc_hex};
            padding: 6px 6px;
            {gradient_border}
            font-weight: bold;
        }}

        QPushButton:hover {{
            background-color: {acc_hex};
            color: {bg_hex};
            {gradient_border_full}
        }}

        QPushButton:disabled {{
            background-color: {dis_bg_hex};
            color: {dis_text_hex};
            border: 1px solid {dis_text_hex};
            font-weight: normal;
        }}

        QPushButton:disabled:hover {{
            background-color: {dis_bg_hex};
            color: {dis_text_hex};
        }}

        QLabel {{
            color: {acc_hex};
        }}

        QToolTip {{
            background-color: {bg_hex};
            color: {acc_hex};
            padding: 6px;
        }}
    """)

def apply_font(app, font, font_file):
    """
    Applies the font to the application.

    If font_file is provided, loads that font file and applies it.
    If font is provided (with a family name), checks if it's a system font and uses it.
    Otherwise, falls back to the default TrixieCyrG font.
    """

    logger = logging.getLogger(__name__)
    default_font_file = "TrixieCyrG-Plain Regular.otf"

    # If a specific font file is provided, load it
    if font_file:
        font_resource = font_file
    elif font and font.family():
        # Check if the font family exists in the system
        font_family = font.family()
        available_families = QFontDatabase.families()

        if font_family in available_families:
            # Font is a system font, just apply it directly
            logger.debug(f"Using system font: {font_family}")
            app.setFont(font)
            return True, font_family
        else:
            # Font family not found, try to load default
            logger.debug(f"Font family '{font_family}' not found in system, using default")
            font_resource = default_font_file
    else:
        font_resource = default_font_file

    # Resolve font_resource: accept Path, absolute path, or relative resource path
    try:
        candidate = Path(font_resource)
        if candidate.is_absolute() and candidate.exists():
            font_path = candidate
        else:
            font_path = Paths.resource(str(font_resource))
    except Exception:
        font_path = Paths.resource(str(font_resource))

    logger.debug(f"Attempting to load font from: {font_path}")
    if not font_path.exists():
        logger.warning(f"Font file not found at: {font_path}")
        return False, str(font_path)

    font_id = QFontDatabase.addApplicationFont(str(font_path))
    if font_id == -1:
        logger.warning(f"QFontDatabase failed to load font: {font_path}")
        return False, str(font_path)

    families = QFontDatabase.applicationFontFamilies(font_id)
    if not families:
        logger.warning(f"No font families returned for: {font_path}")
        return False, str(font_path)

    font_name = families[0]
    if not font:
        font = QFont(font_name, 12)
    else:
        # If a font file was provided, force the family to the loaded one
        font.setFamily(font_name)
    app.setFont(font)

    # Prefer returning the registered family name
    return True, font_name

def update_appearance(app, accent="#C06C84", background="#000000", font=None, font_file=None):
    """Apply a dynamic palette and custom font to the application

    font_file: relative resource path to load instead of the default embedded font.
    """

    apply_palette(app, accent, background)
    return apply_font(app, font, font_file)
