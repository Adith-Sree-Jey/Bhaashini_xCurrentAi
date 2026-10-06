#!/usr/bin/env bash
# Bootstrap a puriyudha dev environment on an x86_64 Linux laptop, with no
# Jetson attached: creates a venv, vendors the upstream platform, installs
# it WITHOUT its ARM/CircuitPython-only dependencies, installs puriyudha
# itself, then self-checks by running the pytest suite and the same
# --dummy-board --list-apps smoke test the acceptance criteria use.
#
# See docs/dev_environment.md for the full rationale behind every step
# below, in particular why vendor/suno-sutra-sw/python/requirements.txt is
# never installed directly (it unconditionally pulls Adafruit-Blinka and a
# dozen adafruit-circuitpython-*/xpt2046_circuitpython packages that are
# unnecessary -- and often not installable -- off a Jetson).
#
# Usage:
#   bash scripts/setup_dev.sh
#
# Env vars (all optional):
#   PURIYUDHA_VENV_DIR      Where to create the venv. Default: ./.venv
#   PURIYUDHA_SKIP_APT      Set to 1 to skip the best-effort `apt-get install`
#                           of system packages pyaudio/opencv-python need.
#   PURIYUDHA_SKIP_SELFTEST Set to 1 to skip running pytest at the end (the
#                           --dummy-board --list-apps smoke test still runs).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

SKIP_APT="${PURIYUDHA_SKIP_APT:-0}"
SKIP_SELFTEST="${PURIYUDHA_SKIP_SELFTEST:-0}"

log()  { printf '\n\033[1;34m==>\033[0m %s\n' "$1"; }
warn() { printf '\033[1;33mWARNING:\033[0m %s\n' "$1" >&2; }
die()  { printf '\033[1;31mERROR:\033[0m %s\n' "$1" >&2; exit 1; }

log "puriyudha dev environment setup"

if [[ "$(uname -s)" != "Linux" ]]; then
  warn "This script targets x86_64 Linux (the pre-Jetson dev target from CLAUDE.md). Detected: $(uname -s). Continuing, but pyaudio/opencv-python system deps below are Linux-specific."
fi

# --- 1. vendor the upstream platform (git submodule) ----------------------
log "Fetching vendored upstream platform: vendor/suno-sutra-sw (git submodule)"
if [[ ! -f "$REPO_ROOT/vendor/suno-sutra-sw/python/setup.py" ]]; then
  git -C "$REPO_ROOT" submodule update --init --depth 1 -- vendor/suno-sutra-sw
else
  echo "  already checked out, skipping (run 'git submodule update --remote -- vendor/suno-sutra-sw' to move to a newer upstream commit)"
fi

# --- 2. best-effort system packages ----------------------------------------
# python3-venv: Debian/Ubuntu split `venv` out of the stdlib package.
# portaudio19-dev / libasound2-dev: pyaudio's build dependency.
# libgl1 / libglib2.0-0: cv2 (opencv-python) needs these to *import*, even
#   headless -- pocketinfer.boards.base imports cv2 at module scope, and
#   that module is on the --dummy-board --list-apps import path.
APT_PACKAGES=(python3-venv python3-dev build-essential portaudio19-dev libasound2-dev libgl1 libglib2.0-0)
if [[ "$SKIP_APT" == "1" ]]; then
  echo "  PURIYUDHA_SKIP_APT=1, skipping system package install"
elif ! command -v apt-get >/dev/null 2>&1; then
  warn "apt-get not found; skipping system package install. If pip install fails below with a missing shared library, install the equivalent of: ${APT_PACKAGES[*]}"
else
  log "Installing system packages needed by pyaudio / opencv-python (best-effort; PURIYUDHA_SKIP_APT=1 to skip)"
  if [[ "$(id -u)" == "0" ]]; then
    apt-get update -qq && apt-get install -y "${APT_PACKAGES[@]}" \
      || warn "apt-get install failed; install manually if pip fails below: ${APT_PACKAGES[*]}"
  elif command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
    sudo apt-get update -qq && sudo apt-get install -y "${APT_PACKAGES[@]}" \
      || warn "apt-get install failed; install manually if pip fails below: ${APT_PACKAGES[*]}"
  else
    warn "No root / passwordless sudo available; skipping automatic system package install. If pip install fails below, run: sudo apt-get install -y ${APT_PACKAGES[*]}"
  fi
fi

# --- 3. pick or create the venv --------------------------------------------
if [[ -n "${VIRTUAL_ENV:-}" ]]; then
  log "Detected an already-active virtualenv at $VIRTUAL_ENV; installing there"
  VENV_DIR="$VIRTUAL_ENV"
else
  VENV_DIR="${PURIYUDHA_VENV_DIR:-$REPO_ROOT/.venv}"
  PY_BIN=""
  for candidate in python3.10 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      PY_BIN="$candidate"
      break
    fi
  done
  [[ -n "$PY_BIN" ]] || die "No python3 interpreter found on PATH."

  DETECTED_VERSION="$("$PY_BIN" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
  if [[ "$DETECTED_VERSION" != "3.10" ]]; then
    warn "Using $PY_BIN ($DETECTED_VERSION). puriyudha targets Python 3.10 (matches JetPack 6.2's cp310 on the Jetson); the codebase avoids 3.11+-only syntax so $DETECTED_VERSION should still work, but install python3.10 for closer parity if you hit a version-specific issue."
  fi

  log "Creating virtualenv at $VENV_DIR with $PY_BIN ($DETECTED_VERSION)"
  if [[ ! -d "$VENV_DIR" ]]; then
    "$PY_BIN" -m venv "$VENV_DIR"
  else
    echo "  $VENV_DIR already exists, reusing it"
  fi
fi

PY="$VENV_DIR/bin/python"
"$PY" -m pip install --upgrade pip wheel --quiet

# --- 4. drift check (informational only, never blocks setup) --------------
log "Checking vendored requirements.txt for untriaged packages"
"$PY" "$REPO_ROOT/scripts/check_requirements_drift.py" || true

# --- 5. install pocketinfer WITHOUT its ARM/CircuitPython dependencies ----
log "Installing vendored pocketinfer package (--no-deps; see constraints/ and docs/dev_environment.md)"
"$PY" -m pip install --no-deps -e "$REPO_ROOT/vendor/suno-sutra-sw/python"

log "Installing pocketinfer's cross-platform runtime dependencies"
"$PY" -m pip install -r "$REPO_ROOT/constraints/x86-dev-requirements.txt"

# --- 6. install puriyudha itself -------------------------------------------
log "Installing puriyudha (editable, with dev/test dependencies)"
"$PY" -m pip install -e "${REPO_ROOT}[dev]"

# --- 7. self-check: the same smoke test the acceptance criteria run -------
log "Smoke test: pocketinfer-service --dummy-board --list-apps"
"$VENV_DIR/bin/pocketinfer-service" --dummy-board --list-apps

if [[ "$SKIP_SELFTEST" == "1" ]]; then
  echo "  PURIYUDHA_SKIP_SELFTEST=1, skipping pytest"
else
  log "Running pytest (hermetic: no network, no Ollama, no real Bhashini service)"
  "$PY" -m pytest
fi

log "Setup complete."
cat <<EOF

  source "$VENV_DIR/bin/activate"
  pytest
  pocketinfer-service --dummy-board --list-apps

This script already ran both of the above once (as a self-check), so
everything is verified end to end. Note that 'bash scripts/setup_dev.sh'
runs in its own subshell -- it cannot leave its venv active in *your* shell
afterwards. Either 'source' the activate script above, or 'source
scripts/setup_dev.sh' instead of 'bash scripts/setup_dev.sh' next time if
you want the venv to stay active where you ran it from.
EOF
