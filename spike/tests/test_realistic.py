import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

from spike.generator import generate, make_invoice, render_invoice


class RealisticTests(unittest.TestCase):
    def test_styles_determinism_and_canonical_json(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for style in ('clean', 'scan', 'photo', 'mixed'):
                for repeat in ('a', 'b'):
                    generate(root / style / repeat, 2, 7, style=style)
                for index in range(1, 3):
                    for suffix in ('png', 'json'):
                        name = f'invoice-{index:02d}.{suffix}'
                        first = (root / style / 'a' / name).read_bytes()
                        self.assertEqual(first, (root / style / 'b' / name).read_bytes())
                        if suffix == 'json':
                            self.assertEqual(json.loads(first), make_invoice(7, index - 1))
                        elif style != 'clean':
                            self.assertNotEqual(first, (root / 'clean' / 'a' / name).read_bytes())
                if style == 'scan':
                    with Image.open(root / style / 'a' / 'invoice-01.png') as image:
                        self.assertEqual(image.getchannel('R').tobytes(), image.getchannel('G').tobytes())

    def test_mixed_demo_has_both_styles_and_layouts(self):
        with tempfile.TemporaryDirectory() as directory:
            generate(directory, 5, 7, style='mixed')
            counts, layouts = {'scan': 0, 'photo': 0}, set()
            for index in range(5):
                path = Path(directory, f'invoice-{index + 1:02d}')
                with Image.open(path.with_suffix('.png')) as image:
                    style = 'scan' if image.getchannel('R').tobytes() == image.getchannel('G').tobytes() else 'photo'
                counts[style] += 1
                layouts.add(json.loads(path.with_suffix('.json').read_text())['layout'])
            self.assertGreaterEqual(min(counts.values()), 2)
            self.assertEqual(layouts, {'layout-a', 'layout-b'})

    def test_printed_formats_vary_without_mutating_truth(self):
        invoice = make_invoice(7, 0)
        invoice['fields']['invoice_date'] = '2026-11-14'
        invoice['fields']['total'] = '1285.58'
        original = copy.deepcopy(invoice)
        dates, money = set(), set()
        actual_text = ImageDraw.ImageDraw.text
        for seed in range(20):
            printed = []

            def capture(draw, xy, text, *args, **kwargs):
                printed.append(text)
                return actual_text(draw, xy, text, *args, **kwargs)

            with patch.object(ImageDraw.ImageDraw, 'text', capture):
                render_invoice(invoice, io.BytesIO(), seed=seed, style='clean')
            dates.update(text.removeprefix('Invoice date: ') for text in printed if text.startswith('Invoice date: '))
            money.update(text for text in printed if text in {'1285.58', '1,285.58', '$1,285.58'})
            self.assertEqual(invoice, original)
        self.assertEqual(dates, {'2026-11-14', '14 Nov 2026', 'Nov 14, 2026', '14/11/2026'})
        self.assertEqual(money, {'1285.58', '1,285.58', '$1,285.58'})
