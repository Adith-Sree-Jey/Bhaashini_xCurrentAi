# Dev environment: running the upstream platform on an x86_64 laptop

We do not have the Jetson Orin Nano until 5 Aug 2026 (CLAUDE.md). Everything
has to be built and tested on a laptop first. This document explains exactly
how `vendor/suno-sutra-sw` (the upstream Suno Sutra platform) is vendored,
why its own `requirements.txt` cannot be installed as-is on x86_64, what we
did about it, and why we did it that way instead of editing upstream files.

## Quickstart

```
bash scripts/setup_dev.sh
```

This creates a venv, vendors and installs the upstream platform, installs
puriyudha, and then runs the full pytest suite plus
`pocketinfer-service --dummy-board --list-apps` as a self-check. If it exits
0, everything worked.

To pick the setup back up in a new shell:

```
source .venv/bin/activate
pytest
pocketinfer-service --dummy-board --list-apps
```

A note on the acceptance line `bash scripts/setup_dev.sh && pytest`: running
the script with `bash` starts a subshell, so its venv cannot stay active in
*your* shell once it exits — that's why the script also runs `pytest`
itself internally as its last step, so `bash scripts/setup_dev.sh` alone
already proves the whole chain end to end. If you want the outer
`&& pytest` to also resolve to the right interpreter, either `source
.venv/bin/activate` first, or run `source scripts/setup_dev.sh` instead of
`bash scripts/setup_dev.sh`.

## How the upstream platform is vendored

`vendor/suno-sutra-sw` is a real git submodule pointing at
`https://github.com/currentai-org/suno-sutra-sw` (MIT licensed), pinned at
commit `89487b718da9eddbc302d93e2a04e79522c67bc3` on `main` as of
2026-07-28. `scripts/setup_dev.sh` runs
`git submodule update --init --depth 1 -- vendor/suno-sutra-sw` the first
time it's run. To move to a newer upstream commit deliberately:

```
git submodule update --remote -- vendor/suno-sutra-sw
git add vendor/suno-sutra-sw
git commit -m "vendor: bump suno-sutra-sw to <sha>"
```

We never edit files inside `vendor/suno-sutra-sw`. Everything below is
additive, in our own repo, alongside the submodule.

## The problem: upstream's requirements.txt is not x86-installable

`vendor/suno-sutra-sw/python/requirements.txt` lists, unconditionally:

```
pyserial
ollama
pyaudio
numpy
SpeechRecognition
vosk
piper-tts
appdirs
opencv-python
psutil
Adafruit-Blinka==9.1.0
adafruit-blinka-displayio==2.3.2
adafruit-circuitpython-bitmap_font==2.4.2
adafruit-circuitpython-busdevice==5.2.17
adafruit-circuitpython-connectionmanager==3.1.8
adafruit-circuitpython-display_button==1.11.8
adafruit-circuitpython-display_shapes==2.10.6
adafruit-circuitpython-display-text==5.0.4
adafruit-circuitpython-ili9341==2.0.4
adafruit-circuitpython-requests==4.1.17
adafruit-circuitpython-rgb-display==3.14.5
adafruit-circuitpython-ticks==1.1.7
adafruit-circuitpython-typing==1.12.3
Adafruit-PlatformDetect==3.88.0
Adafruit-PureIO==1.1.11
xpt2046_circuitpython==1.0.3
```

The last 16 lines are CircuitPython hardware-abstraction packages for the
Jetson's SPI ILI9341 display and XPT2046 resistive touch controller, via
the Adafruit Blinka compatibility layer. They are ARM/embedded-Linux
oriented; several either fail to build or raise at import time when Blinka
can't detect a supported board, which an x86_64 laptop is not.

`vendor/suno-sutra-sw/python/setup.py` hardcodes
`install_requires=open('requirements.txt').read().splitlines()`, so a plain
`pip install vendor/suno-sutra-sw/python` pulls in that entire list with no
way to opt out from the outside, short of editing that file — which we
were asked not to do, and which would be invisible to `git diff` against
upstream and would silently need re-doing on every submodule bump anyway.

## Do we actually need those packages for our dev target?

No. We traced every module-scope import in the vendored `pocketinfer`
package against what `pocketinfer-service --dummy-board --list-apps`
actually executes (`vendor/suno-sutra-sw/python/pocketinfer/service.py`):

- `--list-apps` runs `from pocketinfer.applications import *`, reads
  `ApplicationRegistry._classes`, prints it, and calls `sys.exit(0)` —
  **before** `Board.get_board()` / `DummyBoard(...)` is ever constructed.
- `pocketinfer/applications/__init__.py` glob-imports every module in
  `pocketinfer/applications/`: `base.py`, `registry.py`,
  `hear_the_world.py`, `hear_the_world_en.py`. Between them these import
  `pocketinfer.models.{ollama,piper,vosk,asr,nmt,tts}` and
  `pocketinfer.audio` — none of which import anything Adafruit/CircuitPython
  at module scope.
- `pocketinfer/service.py` also imports `pocketinfer.boards.base` at module
  scope (for the `Board`/`DummyBoard` classes used later in `main()`), and
  `boards/base.py` imports `cv2` (opencv-python) — a real, x86-installable
  dependency — but nothing ARM-specific.
- The only place any of `Adafruit-Blinka` et al. get imported is
  `pocketinfer/boards/jetson.py` (`import Jetson.GPIO as GPIO` at module
  scope) and `pocketinfer/ui/handheld.py` (`displayio`, `terminalio`,
  `adafruit_display_text`, `adafruit_bitmap_font`, `adafruit_button` at
  module scope; `digitalio`/`board`/`fourwire`/`adafruit_ili9341`/
  `xpt2046_circuitpython` only inside `IlI9341HandheldUI.__init__`, i.e. at
  real-hardware bring-up time, not import time). `boards/jetson.py` is only
  ever imported from inside `Board.get_board()`
  (`boards/base.py:196-201`), and `get_board()` is only called by
  `service.py` when `--dummy-board` is **not** passed
  (`service.py:65-68`).

So for our stated dev target — `pocketinfer-service --dummy-board
--list-apps` on a laptop with no Jetson — none of the ARM/CircuitPython
packages are ever imported. We do not need working Blinka on x86 at all;
we just need to stop `pip` from trying to install it.

## What we did (without editing upstream files)

Two additive mechanisms, combined — an optional-dependency split, and a
conftest-level import shim, matching the two techniques CLAUDE.md's
authors anticipated we'd reach for:

**1. Optional-dependency split** (`constraints/x86-dev-requirements.txt`,
`constraints/arm_only_packages.txt`)

`scripts/setup_dev.sh` installs the vendored package with:

```
pip install --no-deps -e vendor/suno-sutra-sw/python
pip install -r constraints/x86-dev-requirements.txt
```

`--no-deps` makes pip build and editable-install the `pocketinfer` package
itself without ever consulting `install_requires` (i.e. without ever
reading the ARM-only tail of upstream's `requirements.txt` as a dependency
to satisfy). `constraints/x86-dev-requirements.txt` is our own file, in our
own repo, listing exactly the cross-platform subset upstream also lists
(`pyserial`, `ollama`, `pyaudio`, `numpy`, `SpeechRecognition`, `vosk`,
`piper-tts`, `appdirs`, `opencv-python`, `psutil`) — deriving from, not
duplicating a fork of, upstream's file. `constraints/arm_only_packages.txt`
documents the excluded packages and why.

`scripts/check_requirements_drift.py` re-parses upstream's
`requirements.txt` on every `setup_dev.sh` run and warns (does not fail) if
it now lists a package neither triage file accounts for — so if a future
submodule bump adds a new dependency, we notice deliberately instead of
`pocketinfer` silently failing to import at some more confusing later
point. Run it by hand any time with:

```
.venv/bin/python scripts/check_requirements_drift.py
```

**2. Conftest-level hardware import shim** (`tests/conftest.py`)

Even though `--dummy-board --list-apps` never touches
`pocketinfer.boards.jetson` or `pocketinfer.ui.handheld`, puriyudha's own
tests may eventually want to import or mock board-HAL-adjacent code from
the vendored package directly. `tests/conftest.py` installs lightweight
`sys.modules` stand-ins for exactly the modules those two files import at
module scope — `Jetson.GPIO`, `displayio`, `terminalio`,
`adafruit_display_text` (`label`, `text_box`), `adafruit_bitmap_font`
(`bitmap_font.load_font`, which `HandheldUI` calls directly in its class
body — i.e. at import time), and `adafruit_button.button` — before any test
module is collected. It checks `importlib.util.find_spec` first and steps
aside if a real implementation is already importable (e.g. if this suite
is ever run for real on a Jetson with Blinka installed).
`tests/test_vendor_import.py` exercises both the split and the shim, so a
regression in either shows up as a normal test failure.

### Default behaviour when pocketinfer is missing: FAIL, not skip

`tests/test_vendor_import.py` used to use `pytest.importorskip("pocketinfer")`
and silently SKIP when the vendored platform wasn't installed. That was a
defect in its own right: on any machine where `scripts/setup_dev.sh` had
failed or was never run, the one test proving the vendored platform imports
correctly reported as a non-failure, so a broken dev environment looked
green in CI or in a quick local `pytest` run.

The default is now to **FAIL** (with a message naming the likely cause and
the fix) when `pocketinfer` cannot be imported. If you deliberately want to
run puriyudha's test suite without vendoring the platform at all -- e.g. a
CI lane that only exercises puriyudha's own modules and intentionally skips
the upstream-platform checks -- opt out explicitly:

```
PURIYUDHA_ALLOW_MISSING_VENDOR=1 pytest
```

With that variable set to `1`, `tests/test_vendor_import.py` skips cleanly
instead of failing. It is unset (and thus FAIL is the behaviour) by default
on purpose -- an environment variable you must remember to set is a much
smaller footgun than a skip you have to remember to notice.

We did **not** stub `digitalio`, `board`, `fourwire`, `adafruit_ili9341`,
or `xpt2046_circuitpython` — those are only imported inside
`IlI9341HandheldUI.__init__` (real SPI bring-up) and
`handheld.py`'s `if __name__ == "__main__"` block, neither of which is on
any import path we exercise. Faking real hardware bring-up would be
speculative complexity with nothing to validate it against; if a later
task needs to unit-test that code path, extend the shim then, deliberately,
against a concrete need.

## System packages

Two of the cross-platform dependencies need system libraries on Debian/
Ubuntu, which `scripts/setup_dev.sh` installs on a best-effort basis via
`apt-get` (skippable with `PURIYUDHA_SKIP_APT=1`, e.g. in a container that
already bakes these in):

- `pyaudio` needs PortAudio headers to build: `portaudio19-dev`,
  `libasound2-dev`.
- `opencv-python` (`cv2`) needs `libgl1` and `libglib2.0-0` to **import**,
  even headless — and `boards/base.py` imports `cv2` at module scope, so
  this is on the `--dummy-board --list-apps` path, not just a nice-to-have.
- `python3-venv` / `python3-dev` / `build-essential`: Ubuntu splits `venv`
  out of the stdlib package, and a couple of the pure-Python-ish
  dependencies (e.g. `psutil`) may need a compiler if no prebuilt wheel
  matches your exact Python build.

If `apt-get` isn't available or you don't have sudo, the script warns and
continues; install the equivalent packages for your distribution manually
if a later `pip install` step fails with a missing shared library.

## Why not a pip constraints file?

A pip constraints file (`-c`) can only pin versions of packages that are
already going to be installed — it cannot remove a package that
`requirements.txt` lists unconditionally. Since upstream's `setup.py` bakes
`install_requires` from `requirements.txt` directly, the only way to keep
`pip install -e vendor/suno-sutra-sw/python` from ever attempting the
ARM-only packages is to not ask pip to resolve its dependencies at all
(`--no-deps`) and supply the real ones ourselves — which is what we did.
