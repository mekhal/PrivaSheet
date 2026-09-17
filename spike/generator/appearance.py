"""Seeded print formatting and Pillow-only paper/camera effects."""

import io
import random
from datetime import date
from decimal import Decimal

from PIL import Image, ImageChops, ImageDraw, ImageFilter

STYLES = ('clean', 'scan', 'photo', 'mixed')


def printed_value(value, key, seed):
    rng = random.Random(f'print:{seed}')
    date_style, money_style = rng.randrange(4), rng.randrange(3)
    if key in ('invoice_date', 'due_date'):
        day = date.fromisoformat(value)
        month = 'Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec'.split()[day.month - 1]
        return (value, f'{day.day:02d} {month} {day.year}',
                f'{month} {day.day}, {day.year}',
                f'{day.day:02d}/{day.month:02d}/{day.year}')[date_style]
    if key in ('unit_price', 'amount', 'subtotal', 'tax', 'total'):
        grouped = f'{Decimal(value):,.2f}'
        return (value, grouped, '$' + grouped)[money_style]
    return value


def choose_style(style, seed, index):
    if style not in STYLES:
        raise ValueError(f'Unknown style: {style}')
    if style == 'mixed':
        # Paired styles guarantee coverage even for short batches.
        offset = random.Random(f'styles:{seed}').randrange(2)
        return ('scan', 'photo')[(index + offset) % 2]
    return style


def lighting(image, rng, strength):
    # A low-resolution illumination field gives smooth, uneven exposure.
    field = Image.new('L', (4, 4))
    field.putdata([rng.randint(255 - strength, 255) for _ in range(16)])
    field = field.resize(image.size, Image.Resampling.BICUBIC).convert('RGB')
    return ImageChops.multiply(image.convert('RGB'), field)


def degrade(image, style, seed):
    if style == 'clean':
        return image
    rng = random.Random(f'capture:{seed}:{style}')
    size = image.size
    if style == 'scan':
        image = image.convert('L').convert('RGB')
        image = lighting(image, rng, 25)
        image = image.rotate(rng.uniform(-1.5, 1.5), Image.Resampling.BICUBIC,
                             fillcolor=(245, 245, 245))
        draw = ImageDraw.Draw(image)
        for _ in range(6500):
            x, y = rng.randrange(size[0]), rng.randrange(size[1])
            shade = rng.randint(140, 225)
            draw.point((x, y), fill=(shade,) * 3)
        edge = Image.new('L', size, 0)
        ImageDraw.Draw(edge).rectangle((3, 3, size[0] - 4, size[1] - 4), outline=55, width=9)
        edge = edge.filter(ImageFilter.GaussianBlur(12))
        image = ImageChops.subtract(image, edge.convert('RGB'))
        image = image.filter(ImageFilter.GaussianBlur(0.35))
        compressed = io.BytesIO()
        image.save(compressed, 'JPEG', quality=rng.randint(64, 78))
        compressed.seek(0)
        with Image.open(compressed) as decoded:
            image = decoded.convert('RGB')
    else:
        paper = lighting(image, rng, 48).convert('RGBA')
        # Pad before inverse projective mapping so the entire sheet stays visible.
        sheet = Image.new('RGBA', size)
        paper.thumbnail((int(size[0] * .83), int(size[1] * .83)), Image.Resampling.LANCZOS)
        sheet.paste(paper, ((size[0] - paper.width) // 2, (size[1] - paper.height) // 2))
        direction = rng.choice((-1, 1))
        sheet = sheet.transform(size, Image.Transform.PERSPECTIVE,
                                (1, .025 * direction, -20 * direction,
                                 .015 * direction, 1, 0,
                                 .000045 * direction, -.000015 * direction),
                                Image.Resampling.BICUBIC)
        sheet = sheet.rotate(rng.uniform(-5, 5), Image.Resampling.BICUBIC)
        image = lighting(Image.new('RGB', size, (83, 75, 66)), rng, 45)
        shadow = Image.new('L', size)
        shadow.paste(sheet.getchannel('A'), (12, 18))
        shadow = shadow.filter(ImageFilter.GaussianBlur(16))
        image.paste((35, 31, 27), mask=shadow)
        image.paste(sheet, mask=sheet.getchannel('A'))
        image = image.resize((int(size[0] * .72), int(size[1] * .72)), Image.Resampling.LANCZOS)
        image = image.filter(ImageFilter.GaussianBlur(.4))
        image = image.resize(size, Image.Resampling.BICUBIC)
    # Keep the SPK-01 A4 canvas contract, including its white outer corner.
    result = Image.new('RGB', size, 'white')
    result.paste(image.resize((size[0] - 16, size[1] - 16), Image.Resampling.LANCZOS), (8, 8))
    return result
