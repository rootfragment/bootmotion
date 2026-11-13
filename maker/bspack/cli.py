import argparse
import os
import sys
from fractions import Fraction

from . import __version__
from .format import COMP_NAMES, Header, PackReader, PackWriter
from .frames import get_resolution, iter_frames


def cmd_pack(a):
    if not os.path.exists(a.input):
        sys.exit("input file not found")
    w, h = get_resolution(a.res)
    fps = Fraction(a.fps).limit_denominator(1001)
    os.makedirs(a.out, exist_ok=True)
    path = os.path.join(a.out, f"{a.name}-{w}x{h}.bsp")

    hdr = Header(w, h, fps.numerator, fps.denominator)   # delta+zlib, black bg   
    wr = PackWriter(path, hdr)
    print(f"Packing {a.input} -> {path}  ({w}x{h} @ {float(fps):g} fps)")
    for frame in iter_frames(a.input, w, h, a.fps, a.duration):
        wr.add_frame(frame)
    raw = wr.raw_total
    total = wr.close()
    if not hdr.frame_count:
        sys.exit("no frames produced")
    print(f"{hdr.frame_count} frames, raw {raw/1e6:.1f} MB -> pack {total/1e6:.1f} MB")


def cmd_info(a):
    h = PackReader(a.file).h
    print(f"resolution : {h.width}x{h.height}")
    print(f"fps        : {h.fps:g}")
    print(f"frames     : {h.frame_count} ({h.frame_count / h.fps:.2f}s)")
    print(f"compression: {COMP_NAMES[h.compression]}")


def cmd_extract(a):
    try:
        from PIL import Image
    except ImportError:
        sys.exit("pip install pillow  (only needed for 'extract')")
    r = PackReader(a.file)
    os.makedirs(a.out, exist_ok=True)
    for i in (0, r.h.frame_count // 2, r.h.frame_count - 1):
        img = Image.frombuffer("RGBA", (r.h.width, r.h.height), r.frame(i), "raw", "BGRA", 0, 1)
        img.convert("RGB").save(os.path.join(a.out, f"frame-{i:04d}.png"))
    print(f"wrote first/middle/last frame to {a.out}/")


def main():
    p = argparse.ArgumentParser(prog="bspack", description="Build .bsp boot-animation packs.")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    k = sub.add_parser("pack", help="video/GIF -> .bsp")
    k.add_argument("input")
    k.add_argument("--name", required=True)
    k.add_argument("--res", help="WIDTHxHEIGHT (default: auto-detect this display)")
    k.add_argument("--fps", default="24")
    k.add_argument("--duration", type=float, help="trim to N seconds")
    k.add_argument("--out", default="out")
    k.set_defaults(fn=cmd_pack)

    i = sub.add_parser("info", help="show pack header")
    i.add_argument("file")
    i.set_defaults(fn=cmd_info)

    e = sub.add_parser("extract", help="dump 3 frames as PNG to check the crop")
    e.add_argument("file")
    e.add_argument("--out", default="extracted")
    e.set_defaults(fn=cmd_extract)

    a = p.parse_args()
    a.fn(a)
