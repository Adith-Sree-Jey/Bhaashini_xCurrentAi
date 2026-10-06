"""Pytest bootstrap for puriyudha.

Every test in this suite must be hermetic: no network, no Ollama, and no
real Bhashini service (CLAUDE.md). This file installs one thing before any
test module is collected: an import shim for the small set of ARM /
CircuitPython hardware modules that the *vendored* upstream platform
(``vendor/suno-sutra-sw``, a git submodule) imports at module scope, and
which are deliberately NOT installed on an x86 dev laptop -- see
``constraints/arm_only_packages.txt`` and ``docs/dev_environment.md`` for
the full rationale.

Why this is needed at all, precisely:

- ``pocketinfer/boards/jetson.py`` does ``import Jetson.GPIO as GPIO`` at
  module scope.
- ``pocketinfer/ui/handheld.py`` does module-scope imports of ``displayio``,
  ``terminalio``, ``adafruit_display_text`` and ``adafruit_button``, AND
  calls ``adafruit_bitmap_font.bitmap_font.load_font(...)`` directly inside
  the ``HandheldUI`` class body (as class attributes ``ICON_FONT`` /
  ``HINDI_FONT``) -- so that call happens at *import* time, not just when
  the class is instantiated.

Neither module is ever imported by ``pocketinfer-service --dummy-board``
(``Board.get_board()``, the only caller of ``pocketinfer.boards.jetson``, is
skipped entirely in dummy-board mode), so this shim is not required for the
`--dummy-board --list-apps` smoke check in scripts/setup_dev.sh. It exists
so that puriyudha's own tests (present and future) can import or exercise
those upstream modules directly -- e.g. to unit-test code that talks to the
board HAL -- without installing the real Adafruit-Blinka stack.

Deliberately NOT stubbed: ``digitalio``, ``board``, ``fourwire``,
``adafruit_ili9341``, ``xpt2046_circuitpython``. Those are only imported
inside ``IlI9341HandheldUI.__init__`` (real SPI hardware bring-up) and the
``if __name__ == "__main__"`` block of handheld.py, both of which are actual
hardware paths this dev shim does not attempt to fake.

If a real implementation of any of these modules is already importable
(e.g. this suite runs on an actual Jetson with Adafruit-Blinka installed),
the shim gets out of the way and the real module is used instead.
"""
import importlib.util
import sys
import types


def _real_module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def _stub_module(name: str, **attrs) -> types.ModuleType:
    if _real_module_available(name):
        return sys.modules.get(name) or importlib.import_module(name)
    mod = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(mod, key, value)
    sys.modules[name] = mod
    return mod


def _install_hardware_import_shim() -> None:
    # pocketinfer/boards/jetson.py: `import Jetson.GPIO as GPIO`
    jetson_pkg = _stub_module("Jetson")
    gpio = _stub_module(
        "Jetson.GPIO",
        TEGRA_SOC="TEGRA_SOC",
        IN="IN",
        OUT="OUT",
        BOTH="BOTH",
        setmode=lambda *a, **k: None,
        setup=lambda *a, **k: None,
        add_event_detect=lambda *a, **k: None,
        input=lambda *a, **k: False,
        output=lambda *a, **k: None,
    )
    jetson_pkg.GPIO = gpio

    # pocketinfer/ui/handheld.py module-scope imports
    class _Group(list):
        def __init__(self, *a, **k):
            super().__init__()
            self.hidden = False
            self.root_group = None

    class _Bitmap:
        def __init__(self, *a, **k):
            pass

    class _Palette(list):
        def __init__(self, n=0, *a, **k):
            super().__init__([0] * n)

    _stub_module("displayio", Group=_Group, Bitmap=_Bitmap, Palette=_Palette)
    _stub_module("terminalio", FONT=object())

    class _Label:
        def __init__(self, *a, **k):
            self.anchor_point = (0, 0)
            self.anchored_position = (0, 0)
            self.text = k.get("text", "")

    class _TextBox(_Label):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)

    adt_pkg = _stub_module("adafruit_display_text")
    adt_pkg.label = _stub_module("adafruit_display_text.label", Label=_Label)
    adt_pkg.text_box = _stub_module("adafruit_display_text.text_box", TextBox=_TextBox)

    # Called at *import* time (HandheldUI.ICON_FONT / HINDI_FONT class
    # attributes), so this must never raise, regardless of whether the
    # .pcf font path passed in actually exists.
    abf_pkg = _stub_module("adafruit_bitmap_font")
    abf_pkg.bitmap_font = _stub_module(
        "adafruit_bitmap_font.bitmap_font",
        load_font=lambda path=None, *a, **k: object(),
    )

    class _Button:
        def __init__(self, *a, **k):
            self.selected = k.get("selected", False)

        def contains(self, point):
            return False

    ab_pkg = _stub_module("adafruit_button")
    ab_pkg.button = _stub_module("adafruit_button.button", Button=_Button)


_install_hardware_import_shim()
