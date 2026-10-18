"""Normalize supplied Figma canvas crops beside browser captures for visual QA."""
from pathlib import Path
import json
from PIL import Image, ImageDraw

root = Path(__file__).resolve().parents[2]
output = root / 'verification' / 'design'
refs = [
    ('welcome', 'image.png', (422, 135, 1559, 990)),
    ('upload', 'Screenshot 2026-10-18 190629.png', (610, 239, 1435, 917)),
    ('review', 'Screenshot 2026-10-18 190756.png', (471, 239, 1297, 956)),
    ('results', 'Screenshot 2026-10-18 190944.png', (604, 109, 1429, 973)),
]

def combine(source, actual, destination):
    actual = actual.resize((source.width, round(actual.height * source.width / actual.width)))
    canvas = Image.new('RGB', (source.width * 2 + 30, max(source.height, actual.height) + 40), '#e6e6e6')
    canvas.paste(source, (0, 40))
    canvas.paste(actual, (source.width + 30, 40))
    draw = ImageDraw.Draw(canvas)
    draw.text((12, 12), 'FIGMA REFERENCE', fill='black')
    draw.text((source.width + 42, 12), 'LIVE IMPLEMENTATION', fill='black')
    canvas.save(destination)

for name, filename, box in refs:
    source = Image.open(root / 'figma images' / filename).crop(box).convert('RGB')
    actual = Image.open(output / f'{name}-desktop.png').convert('RGB')
    combine(source, actual, output / f'comparison-{name}.png')
    print(f'{name}: source crop {source.size}; implementation {actual.size}; CSS width 1440, density 1')

for name, filename, source_box, actual_box in [
    ('hero', 'image.png', (472, 230, 1509, 684), (64, 120, 1376, 696)),
    ('capture-cards', 'Screenshot 2026-10-18 190629.png', (678, 456, 1367, 627), (120, 378, 1320, 666)),
    ('medicine-table', 'Screenshot 2026-10-18 190756.png', (540, 495, 1229, 736), (120, 449, 1320, 858)),
]:
    page_name = {'hero': 'welcome', 'capture-cards': 'upload', 'medicine-table': 'review'}[name]
    selector = {'hero': '.welcome-hero', 'capture-cards': '.capture-grid', 'medicine-table': '.medicine-card'}[name]
    metadata = output / f'{page_name}-desktop.json'
    if metadata.exists():
        box = json.loads(metadata.read_text())[selector]
        actual_box = (round(box['x']), round(box['y']), round(box['x']+box['width']), round(box['y']+box['height']))
    source = Image.open(root / 'figma images' / filename).crop(source_box).convert('RGB')
    actual = Image.open(output / f'{page_name}-desktop.png').crop(actual_box).convert('RGB')
    combine(source, actual, output / f'focus-{name}.png')
