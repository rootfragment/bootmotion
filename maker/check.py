"""
Independent check of a .bsp pack against its source video.

  python3 check.py myvideo.mp4 out/test-1366x768.bsp [--duration 3]

Re-extracts the frames with ffmpeg as PNGs (a different path from the maker's
raw pipe), then compares them to what is stored in the pack.
"""
import glob
import os
import subprocess
import sys
import tempfile

from PIL import Image
from bspack.format import PackReader

ok = True


def check(name, passed, detail=""):
    global ok
    ok &= passed
    print(f"[{'PASS' if passed else 'FAIL'}] {name} {detail}")


def main():
    args = sys.argv[1:]
    duration = None
    if "--duration" in args:
        i = args.index("--duration")
        duration = args[i + 1]
        del args[i:i + 2]
    if len(args) != 2:
        sys.exit(__doc__)
    video, packfile = args

    r = PackReader(packfile)
    h = r.h
    print(f"pack: {h.width}x{h.height} @ {h.fps:g} fps, {h.frame_count} frames\n")

    with tempfile.TemporaryDirectory() as tmp:
        vf = (f"fps={h.fps_num}/{h.fps_den},"
              f"scale={h.width}:{h.height}:force_original_aspect_ratio=increase,"
              f"crop={h.width}:{h.height}")
        cmd = ["ffmpeg", "-loglevel", "error", "-i", video]
        if duration:
            cmd += ["-t", duration]
        cmd += ["-vf", vf, os.path.join(tmp, "f%05d.png")]
        subprocess.run(cmd, check=True)
        refs = sorted(glob.glob(os.path.join(tmp, "f*.png")))

        check("frame count matches independent extraction",
              len(refs) == h.frame_count, f"(pack {h.frame_count}, ffmpeg {len(refs)})")

        n = min(len(refs), h.frame_count)
        worst_mean, worst_max, prev, changed = 0.0, 0, None, 0
        for i in range(n):
            mine = Image.frombuffer("RGBA", (h.width, h.height), r.frame(i),
                                    "raw", "BGRA", 0, 1).convert("RGB").tobytes()
            ref_img = Image.open(refs[i]).convert("RGB")
            check_dim = ref_img.size == (h.width, h.height)
            if not check_dim:
                check("frame dimensions", False, f"(frame {i}: {ref_img.size})")
                break
            ref = ref_img.tobytes()
            diffs = [abs(a - b) for a, b in zip(mine[::97], ref[::97])]  # sampled for speed
            worst_mean = max(worst_mean, sum(diffs) / len(diffs))
            worst_max = max(worst_max, max(diffs))
            if prev is not None and mine != prev:
                changed += 1
            prev = mine

        check("pixels match independent extraction", worst_mean < 3.0,
              f"(worst mean diff {worst_mean:.3f}, worst max {worst_max} out of 255)")
        check("animation actually changes between frames", n < 2 or changed > 0,
              f"({changed}/{max(n - 1, 0)} frame pairs differ)")

    sizes = [s for _, s, _ in r.index]
    raw = h.frame_bytes * h.frame_count
    check("compression is working", sum(sizes) < raw, f"({sum(sizes)/1e6:.1f} MB vs {raw/1e6:.1f} MB raw)")
    print("\nALL CHECKS PASSED" if ok else "\nSOME CHECKS FAILED")
    sys.exit(0 if ok else 1)


main()
