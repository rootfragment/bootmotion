
import glob
import re
import shutil
import subprocess
import sys


def detect_resolution():
    # 1. DRM: first connected connector's preferred mode
    for conn in sorted(glob.glob("/sys/class/drm/card*-*")):
        try:
            if open(f"{conn}/status").read().strip() != "connected":
                continue
            m = re.match(r"(\d+)x(\d+)", open(f"{conn}/modes").readline().strip())
            if m:
                return int(m.group(1)), int(m.group(2))
        except Exception:
            pass
    # 2. framebuffer
    try:
        w, h = open("/sys/class/graphics/fb0/virtual_size").read().strip().split(",")
        return int(w), int(h)
    except Exception:
        pass
    # 3. xrandr (desktop only)
    try:
        m = re.search(r"current (\d+) x (\d+)", subprocess.check_output(["xrandr"], text=True))
        if m:
            return int(m.group(1)), int(m.group(2))
    except Exception:
        pass
    return None

def get_resolution(arg):
    #get res from args, sanitize data and fix
    if arg is None or arg == "auto":
        res = detect_resolution()
        if not res:
            sys.exit("Could not detect display resolution. Pass --res WIDTHxHEIGHT.")
        return res
    m = re.fullmatch(r"(\d+)x(\d+)", arg.lower())
    if not m:
        sys.exit(f"Bad resolution '{arg}', expected WIDTHxHEIGHT (e.g. 1920x1080)")
    return int(m.group(1)), int(m.group(2))


def iter_frames(src, w, h, fps, duration=None):
    # frames sized properly cropped res and fps adjusted
    if not shutil.which("ffmpeg"):
        sys.exit("ffmpeg not found (sudo apt install ffmpeg)")
    vf = (f"fps={fps},"
          f"scale={w}:{h}:force_original_aspect_ratio=increase,"  
          f"crop={w}:{h}")                                         
    cmd = ["ffmpeg", "-loglevel", "error", "-i", src]
    if duration:
        cmd += ["-t", str(duration)]
    cmd += ["-vf", vf, "-f", "rawvideo", "-pix_fmt", "bgra", "-"]

    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    size = w * h * 4
    while True:
        buf = p.stdout.read(size)
        if len(buf) < size:
            break
        yield buf
    p.stdout.close()
    err = p.stderr.read().decode(errors="replace")
    p.wait()
    if p.returncode != 0:
        sys.exit(f"ffmpeg failed: {err}")
