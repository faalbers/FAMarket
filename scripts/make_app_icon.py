"""Render the FAMarket app icon (bar chart under a magnifying glass) to .ico."""
from PIL import Image, ImageDraw
from pathlib import Path

NAVY  = (23, 60, 92, 255)   # same family as FAPortfolio's icon
WHITE = (255, 255, 255, 255)
AMBER = (240, 166, 48, 255)

im = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
d = ImageDraw.Draw(im)
d.rounded_rectangle((32, 32, 992, 992), radius=196, fill=NAVY)
for x, top in ((176, 720), (296, 600), (416, 470), (536, 330)):
    d.rounded_rectangle((x, top, x + 78, 848), radius=24, fill=AMBER)
d.line((150, 856, 700, 856), fill=WHITE, width=26)
d.ellipse((520, 128, 856, 464), outline=WHITE, width=54)
d.line((820, 428, 936, 544), fill=WHITE, width=64)

out = Path(__file__).resolve().parent / "app_icon.ico"
sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]
im.save(out, format="ICO", sizes=sizes)
print("wrote", out, out.stat().st_size, "bytes")
