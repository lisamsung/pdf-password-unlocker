"""Draw the app's small geometric lock icon (no downloaded artwork)."""
from pathlib import Path
from PIL import Image, ImageDraw

folder = Path(__file__).parent / 'assets'
folder.mkdir(exist_ok=True)
image = Image.new('RGBA', (256, 256), (0, 0, 0, 0))
draw = ImageDraw.Draw(image)
draw.rounded_rectangle((4, 4, 252, 252), radius=52, fill='#265FE8')
draw.arc((90, 39, 185, 142), 178, 357, fill='white', width=16)
draw.rounded_rectangle((72, 116, 184, 201), radius=15, fill='white')
draw.ellipse((116, 142, 141, 167), fill='#265FE8')
draw.rounded_rectangle((123, 161, 134, 181), radius=4, fill='#265FE8')
image.save(folder / 'app.ico', sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
