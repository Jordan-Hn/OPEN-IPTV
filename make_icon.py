"""
Generate the application icon (assets/icon.ico and assets/icon.png) from the
brand mark: a rounded teal tile with a play triangle, matching the in-app logo.

Run once with: python make_icon.py
"""

import os

from PIL import Image, ImageDraw

ACCENT = (45, 212, 191, 255)   # #2DD4BF
INK = (4, 33, 30, 255)         # #04211E


def draw(size):
    scale = 4  # supersample for smooth edges, then downscale
    s = size * scale
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = round(s * 0.06)
    radius = round(s * 0.24)
    d.rounded_rectangle([pad, pad, s - 1 - pad, s - 1 - pad], radius=radius, fill=ACCENT)
    cx, cy = s / 2 - s * 0.02, s / 2
    d.polygon([(cx - s * 0.16, cy - s * 0.21),
               (cx - s * 0.16, cy + s * 0.21),
               (cx + s * 0.22, cy)], fill=INK)
    return img.resize((size, size), Image.LANCZOS)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    out = os.path.join(here, "assets")
    os.makedirs(out, exist_ok=True)
    base = draw(256)
    base.save(os.path.join(out, "icon.png"))
    sizes = [16, 24, 32, 48, 64, 128, 256]
    base.save(os.path.join(out, "icon.ico"), sizes=[(n, n) for n in sizes])
    print("Wrote assets/icon.png and assets/icon.ico")


if __name__ == "__main__":
    main()
