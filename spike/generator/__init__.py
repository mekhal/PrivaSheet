"""Deterministic synthetic invoices; all identities and addresses are fictional."""

import json
import random
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SIZE = (1240, 1754)  # A4 millimetres converted to pixels at 150 DPI, rounded.
FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")
DESCRIPTIONS = (
    "Synthetic widget batch",
    "Demo calibration kit",
    "Fictional sample module",
    "Test packaging set",
    "Mock assembly service",
    "Synthetic inspection unit",
)


def make_invoice(seed, index):
    """Return the exact ground-truth schema for a zero-based invoice index."""
    rng = random.Random(f"synthetic-invoice:{seed}:{index}")
    issued = date(2026, 1, 1) + timedelta(days=rng.randrange(365))
    rows = []
    for description in rng.sample(DESCRIPTIONS, rng.randint(2, 6)):
        qty = rng.randint(1, 9)
        price = Decimal(rng.randint(101, 50000)) / Decimal(100)
        rows.append(
            {
                "description": description,
                "qty": str(qty),
                "unit_price": f"{price:.2f}",
                "amount": f"{price * qty:.2f}",
            }
        )
    subtotal = sum((Decimal(row["amount"]) for row in rows), Decimal(0))
    tax = (subtotal * Decimal("0.07")).quantize(Decimal("0.01"), ROUND_HALF_UP)
    return {
        "layout": "layout-a" if index % 2 == 0 else "layout-b",
        "fields": {
            "invoice_no": f"SYN-{index + 1:04d}-{rng.randrange(10000):04d}",
            "invoice_date": issued.isoformat(),
            "due_date": (issued + timedelta(days=30)).isoformat(),
            "vendor_name": f"SYNTHETIC Vendor-{rng.randrange(1000):03d}",
            "customer_name": f"SYNTHETIC Customer-{rng.randrange(1000):03d}",
            "currency": "USD",
            "subtotal": f"{subtotal:.2f}",
            "tax": f"{tax:.2f}",
            "total": f"{subtotal + tax:.2f}",
        },
        "tables": {"line_items": rows},
    }


def render_invoice(invoice, destination):
    """Render only fake data on an A4 white page with embedded 150 DPI metadata."""
    image = Image.new("RGB", SIZE, "white")
    draw = ImageDraw.Draw(image)
    fonts = {}
    ink, accent = "#182334", "#244c70"

    def text(x, y, value, size=24, bold=False, right=False, color=ink):
        key = (size, bold)
        if key not in fonts:
            fonts[key] = ImageFont.truetype(
                str(FONT_DIR / ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")),
                size,
            )
        font = fonts[key]
        if right:
            x -= draw.textlength(value, font=font)
        draw.text((x, y), value, font=font, fill=color)

    def party(x, y, label, name, kind):
        text(x, y, label, 20, True, color=accent)
        text(x, y + 38, name, 25, True)
        text(x, y + 78, f"{kind} ADDRESS - FICTIONAL", 20)
        text(x, y + 110, "Unit ZERO, Imaginary Sector", 22)
        text(x, y + 142, "Test City, Nowhere ZZ-000", 22)

    fields = invoice["fields"]
    text(70, 35, "SYNTHETIC DEMO - ALL DATA FICTIONAL - NOT PAYABLE", 20, True)
    if invoice["layout"] == "layout-a":
        text(70, 105, "INVOICE", 52, True, color=accent)
        party(70, 205, "FROM", fields["vendor_name"], "VENDOR")
        party(70, 425, "BILL TO", fields["customer_name"], "CUSTOMER")
        meta_x, meta_y = 690, 215
    else:
        draw.rectangle((70, 95, 1170, 200), fill="#edf3f8", outline=accent, width=3)
        text(100, 112, "INVOICE", 48, True, color=accent)
        text(1135, 138, fields["invoice_no"], 26, True, right=True)
        party(70, 255, "SUPPLIER", fields["vendor_name"], "VENDOR")
        party(690, 255, "CUSTOMER", fields["customer_name"], "CUSTOMER")
        meta_x, meta_y = 70, 465
    for offset, (label, key) in enumerate(
        (
            ("Invoice no", "invoice_no"),
            ("Invoice date", "invoice_date"),
            ("Due date", "due_date"),
            ("Currency", "currency"),
        )
    ):
        text(meta_x, meta_y + offset * 39, f"{label}: {fields[key]}", 24)

    top = 720
    draw.rectangle((70, top, 1170, top + 58), fill=accent)
    for x, title, right in (
        (90, "Description", False),
        (750, "Qty", True),
        (950, "Unit price", True),
        (1150, "Amount", True),
    ):
        text(x, top + 14, title, 23, True, right, "white")
    for index, row in enumerate(invoice["tables"]["line_items"]):
        y = top + 58 + index * 70
        if index % 2 == 0:
            draw.rectangle((70, y, 1170, y + 70), fill="#f3f6f9")
        text(90, y + 20, row["description"], 23)
        for x, key in ((750, "qty"), (950, "unit_price"), (1150, "amount")):
            text(x, y + 20, row[key], 23, right=True)
        draw.line((70, y + 70, 1170, y + 70), fill="#d4dce5", width=1)
    totals_y = 1260
    for index, (label, key) in enumerate(
        (("Subtotal", "subtotal"), ("Tax (7%)", "tax"), ("Total (USD)", "total"))
    ):
        y = totals_y + index * 60
        if key == "total":
            draw.rectangle((670, y - 8, 1170, y + 48), fill="#edf3f8")
        text(690, y, label, 25, key == "total")
        text(1150, y, fields[key], 25, key == "total", right=True)
    draw.line((70, 1570, 1170, 1570), fill=accent, width=2)
    text(
        70, 1600, "Generated for testing only. No payment or tax identifiers exist.", 22
    )
    image.save(destination, format="PNG", dpi=(150, 150))


def generate(out, count, seed):
    """Write numbered PNG/JSON pairs without consulting time or external data."""
    if count < 1:
        raise ValueError("count must be positive")
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    for index in range(count):
        invoice = make_invoice(seed, index)
        stem = out / f"invoice-{index + 1:02d}"
        stem.with_suffix(".json").write_text(
            json.dumps(invoice, separators=(",", ":")) + "\n", encoding="utf-8"
        )
        render_invoice(invoice, stem.with_suffix(".png"))
