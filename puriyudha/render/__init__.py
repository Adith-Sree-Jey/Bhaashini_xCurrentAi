"""Language templates, pictogram renderer, and 320x240 layout.

Renders a validated, confidence-cleared order as plain speech plus
pictograms in the patient's language. All user-facing strings live in
per-language resource files; never inline a translated string in code. Not
yet implemented.

Every function here is patient-facing (CLAUDE.md: "anything that produces
speech, a screen frame, or a QR payload") and therefore takes a
:class:`puriyudha.schema.ConfirmedOrder`, never a bare
:class:`puriyudha.schema.MedicationOrder` -- see ConfirmedOrder's docstring
for why that distinction is enforced by the type system, not a convention
(finding H3, verification pass). The stubs below exist so T5 and later
cannot be written the wrong way by accident.
"""
from __future__ import annotations

from puriyudha.schema import ConfirmedOrder, require_confirmed_order


def render_explanation(order: ConfirmedOrder, language: str):
    """Render `order` as plain speech text plus pictogram references, in
    `language` (CLAUDE.md language scope: "ta"/"hi" patient-facing output).
    Not yet implemented (T5)."""
    require_confirmed_order(order, caller="render.render_explanation")
    raise NotImplementedError("puriyudha.render.render_explanation is not yet implemented (T5)")


def render_screen_frame(order: ConfirmedOrder, language: str):
    """Lay out `order` for the 320x240 ILI9341 display, in `language`.
    Not yet implemented (T5)."""
    require_confirmed_order(order, caller="render.render_screen_frame")
    raise NotImplementedError("puriyudha.render.render_screen_frame is not yet implemented (T5)")
