"""
SMS segmentation / unit estimator.

SMS are billed per segment. This estimator follows the common GSM rules:
  - GSM-7 encoded messages: 160 chars in the first segment, 153 per segment after.
  - UCS-2 (unicode) messages: 70 chars in the first segment, 67 per segment after.

A message is treated as UCS-2 if it contains any non-GSM-7 character.
Returns the number of SMS segments the message occupies.
"""
GSM_7 = set(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?"
    "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
)
UCS2_FIRST = 70
UCS2_NEXT = 67
GSM7_FIRST = 160
GSM7_NEXT = 153


def _is_ucs2(message):
    return any(ch not in GSM_7 for ch in message)


def segments_for(message):
    """Return the number of SMS segments for a message (min 1 for non-empty)."""
    if not message:
        return 1
    if _is_ucs2(message):
        first, nxt = UCS2_FIRST, UCS2_NEXT
    else:
        first, nxt = GSM7_FIRST, GSM7_NEXT
    if len(message) <= first:
        return 1
    # remaining chars beyond the first segment fit nxt per segment
    remaining = len(message) - first
    return 1 + -(-remaining // nxt)  # ceil division


def total_units(message, recipient_count):
    """Estimate total SMS units for sending a message to recipient_count people."""
    return segments_for(message) * recipient_count
