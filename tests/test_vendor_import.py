"""Regression tests for the x86 dev-environment workaround (see
docs/dev_environment.md and constraints/).

These tests exercise the vendored, upstream ``pocketinfer`` package as
installed by ``scripts/setup_dev.sh``: without Adafruit-Blinka or any other
ARM/CircuitPython package installed, and without a Jetson attached.

Historically this module used ``pytest.importorskip`` and silently SKIPPED
when ``pocketinfer`` was not importable. That was itself a defect: on any
machine where ``scripts/setup_dev.sh`` had failed or never been run, the one
test proving the vendored platform imports correctly reported as a
non-failure (a skip), not a failure -- so a broken dev environment looked
green. Default behaviour is now to FAIL loudly and with an actionable
message. The only way to get the old skip-like behaviour back is the
explicit opt-out below, e.g. for a CI lane that deliberately does not vendor
the platform -- see docs/dev_environment.md.
"""
import importlib
import os

import pytest

#: Set to "1" to explicitly opt out of requiring pocketinfer to be
#: importable (documented in docs/dev_environment.md). Deliberately NOT the
#: default: an unset/absent vendor install should be loud, not silent.
ALLOW_MISSING_VENDOR_ENV = "PURIYUDHA_ALLOW_MISSING_VENDOR"


def _pocketinfer_import_error() -> "Exception | None":
    try:
        importlib.import_module("pocketinfer")
    except ImportError as exc:
        return exc
    return None


_IMPORT_ERROR = _pocketinfer_import_error()
_VENDOR_AVAILABLE = _IMPORT_ERROR is None
_ALLOW_MISSING = os.environ.get(ALLOW_MISSING_VENDOR_ENV) == "1"

_ACTIONABLE_MESSAGE = (
    "pocketinfer (the vendored upstream platform, vendor/suno-sutra-sw) is not "
    "importable, so the tests that prove it imports correctly on this machine "
    "cannot run for real.\n"
    "Most likely cause: `bash scripts/setup_dev.sh` has not been run, or failed "
    "-- see docs/dev_environment.md.\n"
    "This fails by default (rather than skipping) because a silent skip here "
    "previously hid a broken dev environment as a false pass.\n"
    f"To explicitly opt out (e.g. a CI lane that intentionally does not vendor "
    f"the platform), set {ALLOW_MISSING_VENDOR_ENV}=1.\n"
    f"Underlying import error: {_IMPORT_ERROR!r}"
)


if _VENDOR_AVAILABLE:
    # service.py's --list-apps path does the literal statement `from
    # pocketinfer.applications import *`, not a plain `import
    # pocketinfer.applications`. That distinction matters: pocketinfer/
    # applications/__init__.py only sets `__all__` to the list of submodule
    # names in that directory (base, registry, hear_the_world,
    # hear_the_world_en) -- it never imports them itself. Per the Python
    # import system, a package's `__all__` is only consulted by the `import
    # *` form, which then implicitly imports each named submodule; a plain
    # `import pocketinfer.applications` (or `importlib.import_module(...)`)
    # does NOT trigger that and would leave ApplicationRegistry empty. So
    # this test uses the same star-import statement service.py does, at
    # module scope (`import *` is a SyntaxError inside a function body) --
    # which is also why this whole block is gated at module scope rather
    # than inside a test function.
    from pocketinfer.applications import *  # noqa: F401,F403

    def test_applications_package_imports_without_arm_packages():
        """The exact import service.py performs for `--list-apps`.

        This must succeed with none of Adafruit-Blinka / adafruit-circuitpython-*
        / xpt2046_circuitpython installed (see
        constraints/arm_only_packages.txt) -- those packages are never touched
        by this import path.
        """
        applications = importlib.import_module("pocketinfer.applications")
        assert applications is not None

    def test_list_apps_registry_contains_upstream_reference_apps():
        """Mirrors `pocketinfer-service --dummy-board --list-apps`.

        service.py's --list-apps path does `from pocketinfer.applications import
        *` (done at module scope above, for real, the same way service.py does
        it) then reads `ApplicationRegistry._classes`, without ever calling
        `Board.get_board()`.
        """
        from pocketinfer.applications.registry import ApplicationRegistry

        assert "HearTheWorld" in ApplicationRegistry._classes
        assert "HearTheWorldEn" in ApplicationRegistry._classes

    def test_boards_base_imports_without_hardware():
        """`pocketinfer.boards.base` (Board, DummyBoard) must not require a
        Jetson, ALSA hardware, or a camera to *import* -- only cv2/numpy/etc."""
        boards_base = importlib.import_module("pocketinfer.boards.base")
        assert hasattr(boards_base, "DummyBoard")

    def test_jetson_board_module_imports_via_hardware_shim():
        """`pocketinfer.boards.jetson` is never imported by --dummy-board, but
        must still be importable on x86 thanks to the conftest.py hardware
        shim (Jetson.GPIO, displayio, terminalio, adafruit_display_text,
        adafruit_bitmap_font, adafruit_button) -- so puriyudha's own tests can
        exercise or mock board-HAL-adjacent code without a Jetson."""
        jetson = importlib.import_module("pocketinfer.boards.jetson")
        assert hasattr(jetson, "PocketInferDevboardUI")
        assert hasattr(jetson, "PocketInferDemo")

    def test_ui_handheld_imports_via_hardware_shim():
        """`pocketinfer.ui.handheld` calls
        `adafruit_bitmap_font.bitmap_font.load_font(...)` inside the
        `HandheldUI` class body -- i.e. at import time -- so the shim's
        `load_font` stub must not raise."""
        handheld = importlib.import_module("pocketinfer.ui.handheld")
        assert hasattr(handheld, "IlI9341HandheldUI")

elif _ALLOW_MISSING:
    pytest.skip(
        f"pocketinfer not installed; {ALLOW_MISSING_VENDOR_ENV}=1 set, "
        "explicitly opting out (see docs/dev_environment.md)",
        allow_module_level=True,
    )

else:

    def test_vendor_platform_is_importable():
        """Fails loudly (not a skip) when pocketinfer cannot be imported.

        See the module docstring: this replaces a previous
        `pytest.importorskip` that silently skipped, hiding a broken dev
        environment as a non-failure.
        """
        pytest.fail(_ACTIONABLE_MESSAGE, pytrace=False)
