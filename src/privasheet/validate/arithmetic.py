"""Reconcile canonical invoice operands using section 6's applicability rules."""

from collections.abc import Collection
from decimal import Decimal, localcontext
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from privasheet.validate.checks import Issue


def check_arithmetic(
    template: dict, values: dict, unparsed: Collection[str] = ()
) -> list["Issue"]:
    """Return arithmetic issues, retaining failed parsing separately from missing.

    ``values`` contains validated canonical decimals or None. ``unparsed`` lists
    targets whose parsing or grounding failed, so optional operands cannot turn
    failed extraction into a zero. Review nulls represent reported missing values.
    """
    from privasheet.validate.checks import Issue

    issues = []
    fields = {field["key"] for field in template.get("fields", [])}
    field_values = values["fields"]

    def operand(value, target, optional=False):
        if target in unparsed:
            return None
        if value is None:
            return Decimal(0) if optional else None
        return Decimal(value)

    def available(operands, rule, target):
        missing = [name for name, value in operands.items() if value is None]
        if missing:
            issues.append(
                Issue(
                    "RECONCILIATION_UNAVAILABLE",
                    target,
                    f"{rule}: missing or unparsed operands: {', '.join(missing)}.",
                )
            )
        return not missing

    def compare(code, target, calculated, reported, detail):
        if abs(calculated - reported) > tolerance:
            issues.append(Issue(code, target, detail))

    with localcontext() as context:
        context.prec = 28
        tolerance = Decimal(template.get("rules", {}).get("tolerance", "0.01"))
        for table in template.get("tables", []):
            key = table["key"]
            columns = {column["key"] for column in table["columns"]}
            rows = values["tables"][key]
            if {"qty", "unit_price", "amount"} <= columns:
                for index, row in enumerate(rows):
                    prefix = f"{key}[{index}]"
                    operands = {
                        name: operand(row.get(name), f"{prefix}.{name}")
                        for name in ("qty", "unit_price", "amount")
                    }
                    target = f"{prefix}.amount"
                    if available(operands, "LINE_AMOUNT_MISMATCH", target):
                        qty, price, amount = operands.values()
                        calculated = qty * price
                        compare(
                            "LINE_AMOUNT_MISMATCH",
                            target,
                            calculated,
                            amount,
                            f"qty {qty} × unit_price {price} = {calculated}, amount {amount}",
                        )
            if "amount" in columns and "subtotal" in fields:
                operands = {
                    f"{key}[{index}].amount": operand(
                        row.get("amount"), f"{key}[{index}].amount"
                    )
                    for index, row in enumerate(rows)
                }
                subtotal = operand(field_values.get("subtotal"), "subtotal")
                operands["subtotal"] = subtotal
                if available(operands, "SUBTOTAL_MISMATCH", key):
                    amounts = [
                        value for name, value in operands.items() if name != "subtotal"
                    ]
                    calculated = sum(amounts, Decimal(0))
                    expression = " + ".join(str(value) for value in amounts) or "0"
                    compare(
                        "SUBTOTAL_MISMATCH",
                        key,
                        calculated,
                        subtotal,
                        f"sum amount ({expression}) = {calculated}, subtotal {subtotal}",
                    )
        if {"subtotal", "total"} <= fields:
            operands = {
                name: operand(field_values.get(name), name)
                for name in ("subtotal", "total")
            }
            for name in ("discount", "shipping", "tax"):
                operands[name] = (
                    operand(field_values.get(name), name, optional=True)
                    if name in fields
                    else Decimal(0)
                )
            if available(operands, "TOTAL_MISMATCH", "total"):
                subtotal, total, discount, shipping, tax = operands.values()
                calculated = subtotal - discount + shipping + tax
                expression = f"subtotal {subtotal}"
                for name, sign in (("discount", "−"), ("shipping", "+"), ("tax", "+")):
                    if name in fields:
                        expression += f" {sign} {name} {operands[name]}"
                compare(
                    "TOTAL_MISMATCH",
                    "total",
                    calculated,
                    total,
                    f"{expression} = {calculated}, total {total}",
                )
    return issues
