"""Synthetic plate rendering with a real TTF font (closer to real plates than Hershey strokes)."""
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

FONTS = ("/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
         "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")


def _font(size):
    for p in FONTS:
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default()


def plate_img(lines, size=64, yellow=False, border=3, pad=18, gap=12):
    """White (or yellow) Indian-style plate with black bold text, 1 or 2 rows. Returns BGR."""
    font = _font(size)
    boxes = [font.getbbox(ln) for ln in lines]
    tw = max(b[2] - b[0] for b in boxes)
    th = max(b[3] - b[1] for b in boxes)
    w = tw + 2 * pad
    h = len(lines) * th + (len(lines) - 1) * gap + 2 * pad
    bg = (240, 200, 40) if yellow else (245, 245, 245)
    im = Image.new("RGB", (w, h), bg)
    d = ImageDraw.Draw(im)
    if border:
        d.rectangle([2, 2, w - 3, h - 3], outline=(20, 20, 20), width=border)
    y = pad
    for ln, b in zip(lines, boxes):
        d.text((pad - b[0], y - b[1]), ln, font=font, fill=(10, 10, 10))
        y += th + gap
    return cv2.cvtColor(np.array(im), cv2.COLOR_RGB2BGR)


def vehicle_with_plate(plate, size=(700, 520), extra_text=None, plate_scale=1.0):
    """Grey 'vehicle' crop with the plate centered in the lower part (+ optional sticker text)."""
    w, h = size
    veh = np.full((h, w, 3), (90, 95, 100), np.uint8)
    cv2.rectangle(veh, (int(w * 0.09), int(h * 0.08)), (int(w * 0.91), int(h * 0.58)), (60, 65, 70), -1)
    if extra_text:
        cv2.putText(veh, extra_text, (int(w * 0.2), int(h * 0.25)), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (230, 230, 230), 4)
    if plate_scale != 1.0:
        plate = cv2.resize(plate, (int(plate.shape[1] * plate_scale), int(plate.shape[0] * plate_scale)),
                           interpolation=cv2.INTER_AREA)
    ph, pw = plate.shape[:2]
    y0, x0 = h - ph - int(h * 0.04), (w - pw) // 2
    veh[y0:y0 + ph, x0:x0 + pw] = plate
    return veh
