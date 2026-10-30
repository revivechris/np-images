"""Normalize assets/icons/*.png to 128x128 RGBA, draw the generic music icon and
the app icon (assets/app.ico). Run from the repo root: python tools/make_icons.py"""
import glob
import os
import sys

from PIL import Image

sys.path.insert(0, os.getcwd())
from nowplaying_rpc import tray_image  # noqa: E402

ICONS = os.path.join('assets', 'icons')

for f in glob.glob(os.path.join(ICONS, '*.png')):
    im = Image.open(f).convert('RGBA')
    if im.size != (128, 128):
        im = im.resize((128, 128), Image.LANCZOS)
    im.save(f)

big = tray_image().resize((128, 128), Image.LANCZOS)
big.save(os.path.join(ICONS, 'music.png'))
tray_image().resize((256, 256), Image.LANCZOS).save(os.path.join('assets', 'app.ico'), sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (256, 256)])

sheet = Image.new('RGBA', (128 * 7, 128 * 2), (40, 40, 40, 255))
for i, f in enumerate(sorted(glob.glob(os.path.join(ICONS, '*.png')))):
    sheet.paste(Image.open(f), ((i % 7) * 128, (i // 7) * 128), Image.open(f))
sheet.save(os.path.join(os.environ.get('TEMP', '.'), 'nprpc_icons.png'))
