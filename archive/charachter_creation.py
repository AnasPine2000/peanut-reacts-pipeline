import os
import math
import random
import argparse
import subprocess
from PIL import Image, ImageDraw, ImageFilter

def clamp(x, lo, hi):
    return max(lo, min(hi, x))

def lerp(a, b, t):
    return a + (b - a) * t

def smoothstep(edge0, edge1, x):
    x = clamp((x - edge0) / (edge1 - edge0), 0.0, 1.0)
    return x * x * (3 - 2 * x)

def draw_peanut_character(t, size=512, seed=0):
    """
    Draws a simple peanut character on a transparent RGBA image.
    t is time in seconds.
    """
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Colors
    base = (205, 165, 105, 255)
    shadow = (170, 125, 75, 255)
    outline = (120, 80, 45, 255)
    highlight = (255, 235, 200, 110)

    cx, cy = size // 2, size // 2

    # Peanut body: two overlapping ellipses
    top = (cx - int(size*0.28), cy - int(size*0.34), cx + int(size*0.28), cy + int(size*0.06))
    bot = (cx - int(size*0.32), cy - int(size*0.02), cx + int(size*0.32), cy + int(size*0.38))

    # Base fills
    d.ellipse(top, fill=base)
    d.ellipse(bot, fill=base)

    # Soft shadow on one side (simple overlay)
    shadow_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow_layer)
    sd.ellipse((top[0] + int(size*0.04), top[1] + int(size*0.03), top[2], top[3]), fill=shadow)
    sd.ellipse((bot[0] + int(size*0.05), bot[1] + int(size*0.03), bot[2], bot[3]), fill=shadow)
    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(radius=int(size*0.01)))
    img = Image.alpha_composite(img, shadow_layer)
    d = ImageDraw.Draw(img)

    # Outline (draw a few times for thickness)
    for w in range(3):
        d.ellipse((top[0]-w, top[1]-w, top[2]+w, top[3]+w), outline=outline)
        d.ellipse((bot[0]-w, bot[1]-w, bot[2]+w, bot[3]+w), outline=outline)

    # Speckles (consistent positions)
    rng = random.Random(seed)
    speck_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    sp = ImageDraw.Draw(speck_layer)
    speck_color = (150, 105, 60, 140)
    for _ in range(55):
        x = rng.randint(int(size*0.30), int(size*0.70))
        y = rng.randint(int(size*0.18), int(size*0.78))
        r = rng.randint(1, 3)
        sp.ellipse((x-r, y-r, x+r, y+r), fill=speck_color)
    speck_layer = speck_layer.filter(ImageFilter.GaussianBlur(radius=0.6))
    img = Image.alpha_composite(img, speck_layer)
    d = ImageDraw.Draw(img)

    # Highlight blob
    hl = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    hd = ImageDraw.Draw(hl)
    hd.ellipse((cx - int(size*0.22), cy - int(size*0.30),
                cx - int(size*0.02), cy - int(size*0.02)), fill=highlight)
    hl = hl.filter(ImageFilter.GaussianBlur(radius=int(size*0.02)))
    img = Image.alpha_composite(img, hl)
    d = ImageDraw.Draw(img)

    # ---- Facial animation parameters ----
    # Blink every ~4 seconds, blink duration ~0.14s
    blink_period = 4.0
    blink_phase = t % blink_period
    blink = 0.0
    if blink_phase < 0.14:
        # Close then open quickly
        blink = 1.0 - abs((blink_phase / 0.07) - 1.0)  # triangle 0..1..0
        blink = clamp(blink, 0.0, 1.0)

    # Eye look direction (subtle)
    look_x = 0.5 * math.sin(2*math.pi*0.23*t)
    look_y = 0.35 * math.sin(2*math.pi*0.17*t + 1.3)

    # Talking (mouth open/close)
    talk = 0.5 + 0.5 * math.sin(2*math.pi*1.35*t)
    talk = talk * talk  # bias toward smaller openings
    mouth_open = lerp(0.12, 1.0, talk)

    # ---- Eyes ----
    eye_y = cy - int(size * 0.05)
    eye_dx = int(size * 0.09)
    eye_w = int(size * 0.10)
    eye_h = int(size * 0.12)

    # Apply blink by shrinking height
    blink_factor = lerp(1.0, 0.12, blink)  # 1 -> open, ~0.12 -> nearly closed
    eh = max(2, int(eye_h * blink_factor))

    left_eye_center = (cx - eye_dx, eye_y)
    right_eye_center = (cx + eye_dx, eye_y)

    def draw_eye(center):
        ex, ey = center
        # White
        d.ellipse((ex - eye_w//2, ey - eh//2, ex + eye_w//2, ey + eh//2), fill=(255,255,255,255))
        # Outline
        d.ellipse((ex - eye_w//2, ey - eh//2, ex + eye_w//2, ey + eh//2), outline=(70,50,35,255), width=2)

        # Pupil (smaller when blinked)
        pupil_r = int(size * 0.018)
        px = ex + int(look_x * eye_w * 0.20)
        py = ey + int(look_y * eye_h * 0.12)
        # If blinking, keep pupil centered (looks nicer)
        if blink > 0.2:
            px, py = ex, ey

        d.ellipse((px - pupil_r, py - pupil_r, px + pupil_r, py + pupil_r), fill=(20,20,20,255))

        # Tiny highlight dot
        hr = max(1, pupil_r // 3)
        d.ellipse((px - hr + 2, py - hr - 2, px + hr + 2, py + hr - 2), fill=(255,255,255,180))

    draw_eye(left_eye_center)
    draw_eye(right_eye_center)

    # ---- Mouth ----
    mouth_cx = cx
    mouth_cy = cy + int(size * 0.18)

    # Mouth width/height based on mouth_open
    mw = int(size * 0.22)
    mh = int(size * 0.10 * mouth_open)

    # If very small opening, draw a smile curve
    if mouth_open < 0.25:
        # Smile arc
        arc_box = (mouth_cx - mw//2, mouth_cy - int(size*0.02),
                   mouth_cx + mw//2, mouth_cy + int(size*0.12))
        d.arc(arc_box, start=200, end=340, fill=(60,35,25,255), width=4)
    else:
        # Open mouth: dark oval + teeth
        mouth_box = (mouth_cx - mw//2, mouth_cy - mh//2, mouth_cx + mw//2, mouth_cy + mh//2)
        d.ellipse(mouth_box, fill=(70, 20, 25, 255), outline=(40, 10, 12, 255), width=3)

        # Upper teeth (only when open enough)
        teeth_h = int(mh * 0.35)
        teeth_box = (mouth_cx - int(mw*0.42), mouth_cy - mh//2 + 3,
                     mouth_cx + int(mw*0.42), mouth_cy - mh//2 + 3 + teeth_h)
        d.rounded_rectangle(teeth_box, radius=teeth_h//2, fill=(245,245,245,255))

        # Tongue hint
        tongue_box = (mouth_cx - int(mw*0.28), mouth_cy + int(mh*0.05),
                      mouth_cx + int(mw*0.28), mouth_cy + int(mh*0.45))
        d.ellipse(tongue_box, fill=(170, 60, 80, 200))

    # Cheek blush (subtle)
    blush = Image.new("RGBA", (size, size), (0,0,0,0))
    bd = ImageDraw.Draw(blush)
    blush_alpha = int(60 + 30*math.sin(2*math.pi*0.5*t + 0.8))
    blush_color = (255, 120, 140, clamp(blush_alpha, 20, 110))
    for sign in (-1, 1):
        bx = cx + sign*int(size*0.18)
        by = cy + int(size*0.08)
        bd.ellipse((bx-18, by-12, bx+18, by+12), fill=blush_color)
    blush = blush.filter(ImageFilter.GaussianBlur(radius=6))
    img = Image.alpha_composite(img, blush)

    return img

def render_frames(out_dir, duration, fps, canvas, char_size, seed):
    os.makedirs(out_dir, exist_ok=True)
    total_frames = int(round(duration * fps))

    for i in range(total_frames):
        t = i / fps

        # Canvas (transparent)
        frame = Image.new("RGBA", (canvas, canvas), (0,0,0,0))

        # Character image
        char = draw_peanut_character(t, size=char_size, seed=seed)

        # Gentle bounce + wobble
        bounce = int(8 * math.sin(2*math.pi*0.7*t))
        wobble_deg = 4.0 * math.sin(2*math.pi*0.35*t)

        char_rot = char.rotate(wobble_deg, resample=Image.BICUBIC, expand=True)

        # Paste centered
        x = (canvas - char_rot.width) // 2
        y = (canvas - char_rot.height) // 2 + bounce
        frame.alpha_composite(char_rot, (x, y))

        # Save
        frame_path = os.path.join(out_dir, f"{i:04d}.png")
        frame.save(frame_path, "PNG")

def run_ffmpeg_make_gif(frames_dir, fps, out_gif, palette_path):
    # Palette
    subprocess.run([
        "ffmpeg", "-y",
        "-framerate", str(fps),
        "-i", os.path.join(frames_dir, "%04d.png"),
        "-vf", "palettegen=stats_mode=diff",
        palette_path
    ], check=True)

    # GIF
    subprocess.run([
        "ffmpeg", "-y",
        "-framerate", str(fps),
        "-i", os.path.join(frames_dir, "%04d.png"),
        "-i", palette_path,
        "-lavfi", "paletteuse=dither=bayer:bayer_scale=5",
        "-loop", "0",
        out_gif
    ], check=True)

def run_ffmpeg_make_alpha_webm(frames_dir, fps, out_webm):
    # VP9 with alpha (great for overlay later)
    subprocess.run([
        "ffmpeg", "-y",
        "-framerate", str(fps),
        "-i", os.path.join(frames_dir, "%04d.png"),
        "-c:v", "libvpx-vp9",
        "-pix_fmt", "yuva420p",
        "-b:v", "0",
        "-crf", "33",
        out_webm
    ], check=True)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration", type=float, default=30.0)
    ap.add_argument("--fps", type=int, default=12, help="GIF fps; lower = smaller file")
    ap.add_argument("--canvas", type=int, default=512, help="output frame size")
    ap.add_argument("--char", type=int, default=512, help="character render size")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", type=str, default="output")
    ap.add_argument("--make_webm", action="store_true", help="also export alpha webm")
    args = ap.parse_args()

    frames_dir = os.path.join(args.out, "frames")
    os.makedirs(args.out, exist_ok=True)

    print(f"[1/3] Rendering frames to: {frames_dir}")
    render_frames(frames_dir, args.duration, args.fps, args.canvas, args.char, args.seed)

    palette_path = os.path.join(args.out, "palette.png")
    out_gif = os.path.join(args.out, "peanut.gif")

    print(f"[2/3] Creating GIF: {out_gif}")
    run_ffmpeg_make_gif(frames_dir, args.fps, out_gif, palette_path)

    if args.make_webm:
        out_webm = os.path.join(args.out, "peanut_alpha.webm")
        print(f"[3/3] Creating alpha WebM: {out_webm}")
        run_ffmpeg_make_alpha_webm(frames_dir, args.fps, out_webm)
    else:
        print("[3/3] Done.")

if __name__ == "__main__":
    main()
