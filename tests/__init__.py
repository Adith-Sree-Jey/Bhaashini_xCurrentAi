"""Makes ``tests`` a real (non-namespace) package.

Without this file, ``tests/`` has no ``__init__.py`` and Python treats it as
an implicit namespace-package *portion* (PEP 420). CPython's PathFinder does
not stop scanning ``sys.path`` when it finds a namespace portion -- it keeps
looking for a *regular* package (one with a loader, i.e. an ``__init__.py``)
and returns that instead, regardless of where in ``sys.path`` it appears.

On at least one dev machine this repo has been run on, the global Python
install has an unrelated third-party package literally named ``tests``
installed in site-packages (a packaging bug in some other project, not
ours). Because that package has a real ``__init__.py`` and this one didn't,
``import tests`` -- and therefore ``from tests._fake_http import ...`` in
tests/test_bhashini_client.py and tests/test_ollama_client.py -- silently
resolved to *that* unrelated package instead of this directory, raising
``ModuleNotFoundError: No module named 'tests._fake_http'``.

Giving this directory its own ``__init__.py`` makes it a regular package
too, so import resolution stops at the first match in ``sys.path`` order --
and combined with ``pythonpath = ["."]`` in pyproject.toml (which puts the
repo root at the front of sys.path before collection), this package always
wins. See docs/dev_environment.md for the full writeup.
"""
