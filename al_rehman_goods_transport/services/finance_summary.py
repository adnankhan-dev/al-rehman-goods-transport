def _safe_amount(value):
    return float(value or 0.0)


def balance_summary(entity_type, amount):
    value = _safe_amount(amount)
    is_contract_side = entity_type == "contractor"

    if value > 0:
        label = "To Receive" if is_contract_side else "To Pay"
        tone = "positive"
        description = "Amount expected from contractor." if is_contract_side else "Amount payable by company."
    elif value < 0:
        label = "Advance Received" if is_contract_side else "Advance Paid"
        tone = "warning"
        description = "Contractor has already paid in advance." if is_contract_side else "Company has already prepaid this account."
    else:
        label = "Settled"
        tone = "neutral"
        description = "No open balance on this account."

    return {
        "raw_amount": value,
        "amount": abs(value),
        "label": label,
        "tone": tone,
        "description": description,
        "signed_amount": value,
    }
