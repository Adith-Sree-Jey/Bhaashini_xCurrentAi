"""QR payload and audio artifact issuance for the final counselled record.

Not yet implemented.

Every function here is patient-facing (CLAUDE.md: "anything that produces
speech, a screen frame, or a QR payload") and therefore takes a
:class:`puriyudha.schema.ConfirmedOrder`, never a bare
:class:`puriyudha.schema.MedicationOrder` -- see that class's docstring
(finding H3, verification pass). The stubs below exist so T5 and later
cannot be written the wrong way by accident.
"""
from __future__ import annotations

from puriyudha.schema import ConfirmedOrder, require_confirmed_order


def issue_qr_payload(order: ConfirmedOrder) -> bytes:
    """Encode `order` as the final QR payload. Not yet implemented (T5)."""
    require_confirmed_order(order, caller="issue.issue_qr_payload")
    raise NotImplementedError("puriyudha.issue.issue_qr_payload is not yet implemented (T5)")


def issue_audio_artifact(order: ConfirmedOrder, language: str) -> bytes:
    """Synthesize the final spoken counselling audio for `order`, in
    `language`. Not yet implemented (T5)."""
    require_confirmed_order(order, caller="issue.issue_audio_artifact")
    raise NotImplementedError("puriyudha.issue.issue_audio_artifact is not yet implemented (T5)")
