"""The pocketinfer application entry point for puriyudha.

This module is intentionally thin: it wires the board HAL (via the vendored
``pocketinfer`` platform) to the pipeline modules above and contains no
business logic of its own. It is not yet implemented; the modules under
``puriyudha/`` are built and tested independently of ``pocketinfer`` first,
behind interfaces that make the on-device swap zero-code (see CLAUDE.md).

Deliberately does not import ``pocketinfer`` at module scope yet: once the
real application class is written here it will subclass
``pocketinfer.applications.base.BaseApplication`` and register itself with
``@RegisterApplication({...})``, matching the pattern used by the upstream
``HearTheWorld`` application in
``vendor/suno-sutra-sw/python/pocketinfer/applications/hear_the_world.py``.
"""
