# bootmotion

A Plymouth alternative for Linux boot animations. The plan has two parts:

1. **`maker/`** (done): a Python tool that turns a video or GIF into a `.bsp` animation pack, cropped to your display resolution.
2. **A boot-time player** (not started): a small C program that reads a `.bsp` and draws it on screen during boot.

Right now only the maker exists. Nothing in this repo touches your boot process yet.

## Status

| Part | State |
|---|---|
| `.bsp` file format (v1) | done, specified in `docs/FORMAT.md` |
| Maker: video/GIF to `.bsp` | done, tested |
| Maker: `info` and `extract` commands | done |
| `check.py` verification script | done, tested |
| Player (framebuffer, then DRM/KMS) | not started |
| Quit signal, timeout, initramfs hook | not started |

## Requirements

- Python 3.9+
- `ffmpeg` on your PATH
- Pillow, only for the `extract` command and `check.py`

On Ubuntu, `pip` refuses system-wide installs, so use a virtual environment:

```bash
sudo apt install ffmpeg
python3 -m venv .venv
source .venv/bin/activate
pip install pillow
```

## Usage

Run everything from inside `maker/`:

```bash
cd maker

# make a pack (auto-detects this display's resolution if --res is omitted)
python3 -m bspack pack boot.mp4 --name test --res 1366x768 --fps 24 --duration 3

# read the header back
python3 -m bspack info out/test-1366x768.bsp

# dump the first, middle and last frame as PNGs to check the crop
python3 -m bspack extract out/test-1366x768.bsp

# verify the pack against an independent ffmpeg extraction
python3 check.py boot.mp4 out/test-1366x768.bsp --duration 3
```

Pass the same `--duration` to `check.py` that you used when packing, or the frame counts will not match.

### `pack` options

| Option | Meaning |
|---|---|
| `--name NAME` | required; output file is `out/NAME-WIDTHxHEIGHT.bsp` |
| `--res WIDTHxHEIGHT` | target size; omit to auto-detect the display |
| `--fps N` | playback rate, default 24 |
| `--duration S` | trim the input to S seconds |
| `--out DIR` | output folder, default `out` |

## How it works

- **Crop:** the video is scaled up just enough to cover the target size, then centre-cropped to exactly `WIDTH x HEIGHT`. A 16:9 video on a 4:3 screen loses its left and right edges. There are no other fit modes.
- **Resolution detection:** reads `/sys/class/graphics/fb0/virtual_size`, then the first DRM connector's mode list, then `xrandr`. If none work, it asks you to pass `--res`. On a VM it reports the VM's screen, not the host's. Pass `--res` when packing for another machine.
- **Pixel layout:** frames are stored as XRGB8888 (bytes B, G, R, X in memory), the layout a DRM dumb buffer expects, so a player can copy them straight to the screen.
- **Compression:** each frame is XORed with the previous one and compressed with zlib, with a full keyframe every 30 frames. The file format also allows raw and plain-zlib frames, but the CLI always uses delta.
- **Playback:** one pass, then the last frame is held. There is no loop option.

## Verified results

On the author's machine, `boot.mp4` packed at 1366x768, 24 fps, 3 seconds:

- 72 frames, 302.1 MB raw down to 38.3 MB in the pack
- `check.py`: frame count matches ffmpeg's independent extraction, and pixels differ by at most 4 out of 255 (colour-conversion rounding)

`check.py` proves the pack matches the video under the same crop settings. It cannot tell you whether the crop looks right, so look at the `extract` output as well.

## Known limits

- The maker has only been run with an explicit `--res`. Auto-detection is untested on real hardware.
- Compression ratio depends on the video. Heavy motion packs much larger than mostly static footage.
- The player must hold two frame buffers (current and previous) to decode delta frames.
- Packs are fixed to one resolution. Make one pack per display size.

## Project layout

```
README.md
docs/FORMAT.md           byte-level .bsp spec; the contract the player will implement
maker/
├── pyproject.toml
├── check.py             independent pack verification
└── bspack/
    ├── __init__.py
    ├── __main__.py      lets you run python3 -m bspack
    ├── cli.py           pack | info | extract
    ├── format.py        .bsp header, writer, reader
    └── frames.py        resolution detection and ffmpeg centre crop
```

## Next steps

1. C player, stage 1: read a `.bsp`, map `/dev/fb0`, play once from a text console.
2. Switch the display backend to DRM/KMS for vsync.
3. Add a quit signal, a timeout fallback, and an initramfs hook so it runs at boot.
