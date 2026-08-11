from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
LOGO_MODULE_PATH = REPO_ROOT / "files" / "speed-of-cinnamon@H234598" / "SettingsLogo.py"


class _FakePixbuf:
    def __init__(self, width: int, height: int) -> None:
        self._width = width
        self._height = height

    def get_width(self) -> int:
        return self._width

    def get_height(self) -> int:
        return self._height


def _load_logo_module() -> types.ModuleType:
    settings_widgets = types.ModuleType("JsonSettingsWidgets")

    class SettingsWidget:
        pass

    settings_widgets.SettingsWidget = SettingsWidget
    gi_module = types.ModuleType("gi")
    repository_module = types.ModuleType("gi.repository")
    gi_module.repository = repository_module
    repository_module.Gdk = types.SimpleNamespace()
    repository_module.GdkPixbuf = types.SimpleNamespace()
    repository_module.Gtk = types.SimpleNamespace()
    replacements = {
        "JsonSettingsWidgets": settings_widgets,
        "gi": gi_module,
        "gi.repository": repository_module,
    }
    previous = {name: sys.modules.get(name) for name in replacements}
    sys.modules.update(replacements)
    try:
        spec = importlib.util.spec_from_file_location("speed_of_cinnamon_settings_logo_test", LOGO_MODULE_PATH)
        if spec is None or spec.loader is None:
            raise RuntimeError("could not load SettingsLogo.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for name, previous_module in previous.items():
            if previous_module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous_module


class SettingsLogoFitTest(unittest.TestCase):
    def test_logo_fit_respects_width_height_and_maximum_bounds(self) -> None:
        module = _load_logo_module()
        logo = module._ResponsiveLogo.__new__(module._ResponsiveLogo)
        logo._source_pixbuf = _FakePixbuf(1000, 500)
        logo.max_width = 720
        logo.max_height = 220

        self.assertEqual(logo._fit_size_for_allocation(1000, 100), (200, 100))
        self.assertEqual(logo._fit_size_for_allocation(1000, 300), (440, 220))
        self.assertEqual(logo._fit_size_for_allocation(100, 100), (100, 50))


if __name__ == "__main__":
    unittest.main()
