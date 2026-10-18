import re
from datetime import datetime
from uuid import uuid4

from uagents import Context, Protocol
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement,
    ChatMessage,
    TextContent,
    chat_protocol_spec,
)

upi_shield_protocol = Protocol(spec=chat_protocol_spec)

AMOUNT_RE = re.compile(
    r"amount\s*:?\s*(?:rs\.?|inr|₹)?\s*([\d,]+(?:\.\d+)?)", re.IGNORECASE
)
UTR_RE = re.compile(r"\butr\s*:?\s*([a-z0-9]+)", re.IGNORECASE)
EXPECTED_RE = re.compile(
    r"expected\s*:?\s*(?:rs\.?|inr|₹)?\s*([\d,]+(?:\.\d+)?)", re.IGNORECASE
)
TIME_RE = re.compile(r"\btime\s*:?\s*(\d{1,2}[:.]\d{2}(?::\d{2})?)", re.IGNORECASE)

HELP_TEXT = (
    "I screen UPI payments for fraud signals. Send your payment like this:\n\n"
    "amount 500 utr 123456789012 time 21:05 expected 500\n\n"
    "I will check:\n"
    "• The UTR is exactly 12 digits\n"
    "• The amount matches the expected amount\n"
    "• The UTR has not been checked before (duplicate detection)\n\n"
    "You will get a risk level (Low / Medium / High) with reasons."
)


def create_text_chat(text: str) -> ChatMessage:
    content = [TextContent(type="text", text=text)]
    return ChatMessage(timestamp=datetime.utcnow(), msg_id=uuid4(), content=content)


def _to_number(raw):
    try:
        return float(raw.replace(",", ""))
    except ValueError:
        return None


def parse_payment(text: str) -> dict:
    amount_match = AMOUNT_RE.search(text)
    utr_match = UTR_RE.search(text)
    expected_match = EXPECTED_RE.search(text)
    time_match = TIME_RE.search(text)
    return {
        "amount": _to_number(amount_match.group(1)) if amount_match else None,
        "utr": utr_match.group(1).upper() if utr_match else None,
        "expected": _to_number(expected_match.group(1)) if expected_match else None,
        "time": time_match.group(1) if time_match else None,
    }


def evaluate_payment(fields: dict, seen_before: bool):
    """Return (risk, reasons, passed_checks) for the parsed payment fields."""
    checks = []
    reasons = []
    high = False
    medium = False

    utr = fields.get("utr")
    utr_valid = bool(utr and re.fullmatch(r"\d{12}", utr))

    if not utr:
        high = True
        reasons.append("UTR is missing — the transaction reference cannot be verified.")
    elif not utr_valid:
        high = True
        reasons.append(f"UTR '{utr}' is not exactly 12 digits ({len(utr)} characters).")
    else:
        checks.append("UTR is exactly 12 digits.")

    if utr_valid:
        if seen_before:
            high = True
            reasons.append(
                "This UTR was already checked before — possible duplicate or replayed payment."
            )
        else:
            checks.append("UTR has not been seen in any previous check.")

    amount = fields.get("amount")
    expected = fields.get("expected")
    if amount is None or expected is None:
        medium = True
        reasons.append(
            "Amount or expected amount missing — the amount match could not be verified."
        )
    elif abs(amount - expected) < 0.005:
        checks.append(f"Amount {amount:g} matches expected {expected:g}.")
    else:
        medium = True
        reasons.append(f"Amount {amount:g} does not match expected {expected:g}.")

    if high:
        risk = "HIGH"
    elif medium:
        risk = "MEDIUM"
    else:
        risk = "LOW"
    return risk, reasons, checks


def build_reply(fields: dict, risk: str, reasons: list, checks: list) -> str:
    emoji = {"LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🔴"}[risk]
    lines = [f"{emoji} UPI Shield — Risk: {risk}", "", "Payment details:"]

    amount = fields.get("amount")
    expected = fields.get("expected")
    lines.append(f"• Amount: {amount:g}" if amount is not None else "• Amount: not provided")
    lines.append(
        f"• Expected: {expected:g}" if expected is not None else "• Expected: not provided"
    )
    lines.append(f"• UTR: {fields.get('utr') or 'not provided'}")
    if fields.get("time"):
        lines.append(f"• Time: {fields['time']}")

    if checks:
        lines.append("")
        lines.append("Passed checks:")
        for check in checks:
            lines.append(f"✅ {check}")

    if reasons:
        lines.append("")
        lines.append("Risk reasons:")
        for reason in reasons:
            lines.append(f"⚠️ {reason}")

    if risk == "LOW":
        lines.append("")
        lines.append("All checks passed. This payment looks safe.")
    elif risk == "MEDIUM":
        lines.append("")
        lines.append("Review this payment carefully before accepting it.")
    else:
        lines.append("")
        lines.append("Do not accept this payment without further verification.")

    return "\n".join(lines)


@upi_shield_protocol.on_message(ChatMessage)
async def handle_message(ctx: Context, sender: str, msg: ChatMessage):
    await ctx.send(
        sender,
        ChatAcknowledgement(timestamp=datetime.now(), acknowledged_msg_id=msg.msg_id),
    )

    text = msg.text()
    if not text:
        return

    fields = parse_payment(text)
    if fields["amount"] is None and fields["utr"] is None:
        await ctx.send(sender, create_text_chat(HELP_TEXT))
        return

    utr = fields["utr"]
    utr_valid = bool(utr and re.fullmatch(r"\d{12}", utr))
    seen_key = f"utr_seen_{utr}" if utr else ""
    seen_before = bool(utr_valid and ctx.storage.get(seen_key))

    risk, reasons, checks = evaluate_payment(fields, seen_before)

    if utr_valid and not seen_before:
        ctx.storage.set(seen_key, True)

    reply = build_reply(fields, risk, reasons, checks)
    await ctx.send(sender, create_text_chat(reply))


@upi_shield_protocol.on_message(ChatAcknowledgement)
async def handle_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
    pass
