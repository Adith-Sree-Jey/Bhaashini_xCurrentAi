# Upstream bug-fix patches

Three `git format-patch` files against `currentai-org/suno-sutra-sw`, each
fixing one of the bugs CLAUDE.md flags ("Known upstream bugs (do not
replicate, and we will PR fixes)"). All three are generated from, and
verified (`git apply --check`) against, the exact commit
`vendor/suno-sutra-sw` is pinned to:
`89487b718da9eddbc302d93e2a04e79522c67bc3` (`main`, 2026-07-28).

**Nothing has been pushed or opened as a PR.** These are drafts for a
human to review and send upstream (e.g.
`git am *.patch` against a clone of `currentai-org/suno-sutra-sw`, then
push a branch and open each as its own PR).

To try one locally:

```
cd vendor/suno-sutra-sw   # or any clone of currentai-org/suno-sutra-sw at 89487b7
git am ../../patches/0001-fix-nmt-missing-imports.patch
```

## PR 1 - `0001-fix-nmt-missing-imports.patch`

**Title:** `models/nmt.py: fix NameError on Nmt.verify() fallback path`

**Files:** `python/pocketinfer/models/nmt.py`

**Description:**

`Nmt.verify()` health-checks `http://localhost:11400/health`, and on a
connection error falls back to restarting the service and polling until it
comes up:

```python
except requests.exceptions.ConnectionError:
    print("Connection Error, trying to launch model")
check_output('systemctl restart bhashini_models.service', shell=True)
start = time.time()
while time.time() - start < 60.0:
    ...
```

but the module only does `import requests` - neither `subprocess` /
`check_output` nor `time` is imported. Every call to `verify()` that
actually reaches this fallback (i.e. exactly the case the fallback exists
to handle - the Bhashini models service isn't up yet) raises
`NameError: name 'check_output' is not defined` instead of restarting the
service. `models/asr.py` implements the identical pattern correctly (`import
time` + `from subprocess import check_output`); this patch just brings
`nmt.py` in line with it.

**Impact:** any application that lists `nmt` in its `METADATA["models"]`
(e.g. `HearTheWorld`) fails at startup with a `NameError` instead of a
useful error whenever `bhashini_models.service` isn't already running,
which will be the common case on a freshly booted device.

## PR 2 - `0002-fix-tts-missing-imports.patch`

**Title:** `models/tts.py: fix NameError on Tts.verify() fallback path`

**Files:** `python/pocketinfer/models/tts.py`

**Description:** The same bug as PR 1, in `Tts.verify()` instead of
`Nmt.verify()` - same missing `import time` / `from subprocess import
check_output`, same fallback path, same fix, same `models/asr.py`
reference implementation.

**Impact:** identical to PR 1, for any application that lists `tts` in its
model dependencies.

## PR 3 - `0003-fix-hear-the-world-service-dependency-typo.patch`

**Title:** `applications/hear_the_world.py: fix bhashini_models typo in service_dependencies`

**Files:** `python/pocketinfer/applications/hear_the_world.py`

**Description:**

`HearTheWorld`'s `@RegisterApplication` metadata declares:

```python
"service_dependencies": ["ollama", "bashini_models"],
```

but the actual systemd unit is `bhashini_models.service` - spelled
correctly everywhere else in the codebase (`models/asr.py`,
`models/nmt.py`, `models/tts.py`'s `verify()` methods, and
`rootfs/roles/indic`, which installs the unit). This patch corrects the
typo to `"bhashini_models"`.

**Impact:** `service_dependencies` isn't read anywhere in
`python/pocketinfer` today (`BaseApplication.verify_dependencies()` reads
`METADATA["models"]`, not `METADATA["service_dependencies"]`), so nothing
currently breaks from this specific typo. It's still worth fixing before
anything - tooling, docs generation, a future health-check pass - starts
trusting `service_dependencies` as the source of truth for which systemd
units an application needs; a misspelled entry would silently no-op
instead of surfacing a missing dependency.
