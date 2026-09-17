import json
import subprocess
import tempfile
import unittest
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
import sys

from PIL import Image
from spike.generator import make_invoice


class GeneratorTests(unittest.TestCase):
    def test_schema_and_arithmetic_many_seeds(self):
        layouts, row_counts = set(), set()
        for seed in range(100):
            for index in range(5):
                invoice = make_invoice(seed, index)
                self.assertEqual(set(invoice), {"layout", "fields", "tables"})
                fields = invoice["fields"]
                self.assertEqual(set(fields), {
                    "invoice_no", "invoice_date", "due_date", "vendor_name",
                    "customer_name", "currency", "subtotal", "tax", "total",
                })
                self.assertEqual(fields["currency"], "USD")
                self.assertGreater(date.fromisoformat(fields["due_date"]),
                                   date.fromisoformat(fields["invoice_date"]))
                self.assertEqual(set(invoice["tables"]), {"line_items"})
                rows = invoice["tables"]["line_items"]
                self.assertTrue(2 <= len(rows) <= 6)
                row_counts.add(len(rows))
                subtotal = Decimal("0")
                for row in rows:
                    self.assertEqual(set(row), {"description", "qty", "unit_price", "amount"})
                    self.assertTrue(all(isinstance(value, str) for value in row.values()))
                    self.assertEqual(Decimal(row["amount"]),
                                     Decimal(row["qty"]) * Decimal(row["unit_price"]))
                    for key in ("unit_price", "amount"):
                        self.assertRegex(row[key], r"^\d+\.\d{2}$")
                    subtotal += Decimal(row["amount"])
                tax = (subtotal * Decimal("0.07")).quantize(Decimal("0.01"), ROUND_HALF_UP)
                for key, expected in (("subtotal", subtotal), ("tax", tax),
                                      ("total", subtotal + tax)):
                    self.assertEqual(fields[key], f"{expected:.2f}")
                layouts.add(invoice["layout"])
        self.assertEqual(layouts, {"layout-a", "layout-b"})
        self.assertEqual(row_counts, set(range(2, 7)))

    def test_cli_files_reproducibility_and_png(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            for output in (first, second):
                subprocess.run([sys.executable, "-m", "spike.generator", "--out", output,
                                "--count", "5", "--seed", "42"], check=True)
            expected = {f"invoice-{i:02d}.{ext}" for i in range(1, 6) for ext in ("png", "json")}
            self.assertEqual({p.name for p in Path(first).iterdir()}, expected)
            layouts = set()
            for name in sorted(expected):
                path = Path(first, name)
                self.assertEqual(path.read_bytes(), Path(second, name).read_bytes())
                if path.suffix == ".json":
                    self.assertEqual(len(path.read_text().splitlines()), 1)
                    data = json.loads(path.read_text())
                    self.assertEqual(data, make_invoice(42, int(path.stem[-2:]) - 1))
                    layouts.add(data["layout"])
                else:
                    with Image.open(path) as image:
                        self.assertEqual(image.size, (1240, 1754))
                        self.assertAlmostEqual(image.info["dpi"][0], 150, places=1)
                        self.assertEqual(image.getpixel((0, 0)), (255, 255, 255))
            self.assertEqual(layouts, {"layout-a", "layout-b"})


if __name__ == "__main__":
    unittest.main()
