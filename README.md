# bootmotion

A Plymouth alternative for Linux boot animations. It has two parts:

1. **`maker/`** (Python): turns a video or GIF into a `.bsp` animation pack, cropped to your display resolution.
2. **`player/`** (C): reads a `.bsp` and draws it on the Linux framebuffer.

The player currently runs only when you start it by hand from a text console. Nothing in this repo touches your boot process yet.

## Status

| Part | State |
|---|---|
| `.bsp` file format (v1) | done, specified in `docs/FORMAT.md` |
| Maker: video/GIF to `.bsp`, `info`, `extract` | done, tested |
| `check.py` pack verification | done, tested |
| Player: framebuffer (`/dev/fb0`) | done; works on the author's laptop from a text console |
| Player: quit signal, timeout, cursor hiding | done |
| Player: framebuffer swap handling | written and tested on a fake framebuffer; not yet confirmed on real hardware |
| Early boot in QEMU | `/dev/fb0` exists at ~1 s; the animation plays once the kernel has a screen console |
| Player: DRM/KMS backend (vsync) | not started |
| systemd service, initramfs hook, content check | not started |

## Requirements

- Python 3.9+, `ffmpeg` on your PATH
- Pillow, only for `extract` and `check.py`
- To build the player: a C compiler, `make`, and the zlib headers

On Ubuntu, `pip` refuses system-wide installs, so use a virtual environment:

```bash
sudo apt install ffmpeg build-essential zlib1g-dev
python3 -m venv .venv
source .venv/bin/activate
pip install pillow
```

## Usage

### 1. Make a pack

Run from inside `maker/`:

```bash
cd maker
python3 -m bspack pack boot.mp4 --name boot --res 1920x1200 --fps 24 --duration 3
python3 -m bspack info out/boot-1920x1200.bsp
python3 -m bspack extract out/boot-1920x1200.bsp      # PNGs of the first, middle and last frame
python3 check.py boot.mp4 out/boot-1920x1200.bsp --duration 3
```

Pass the same `--duration` to `check.py` that you used when packing, or the frame counts will not match.

| `pack` option | Meaning |
|---|---|
| `--name NAME` | required; output is `out/NAME-WIDTHxHEIGHT.bsp` |
| `--res WIDTHxHEIGHT` | target size; omit to auto-detect the display |
| `--fps N` | playback rate, default 24 |
| `--duration S` | trim the input to S seconds |
| `--out DIR` | output folder, default `out` |

Use your screen's **visible** resolution. To check it:

```bash
cat /sys/class/drm/card*-*/status        # which connector is "connected"
cat /sys/class/drm/card*-*/modes | head  # first line of that connector is its native size
```

### 2. Build and run the player

```bash
cd player
make
```

The player draws to the framebuffer, which a desktop session covers, so it only shows on a text console. This command switches to console 3, plays the animation, then switches back:

```bash
cur=$(sed 's/tty//' /sys/class/tty/tty0/active)
sudo sh -c "chvt 3; sleep 1; ./player ../maker/out/boot-1920x1200.bsp; chvt $cur"
```

Or switch by hand with Ctrl+Alt+F3 (you may need Fn held on laptops), log in, and run `sudo ./player path/to/file.bsp`. Ctrl+C quits early. It needs `sudo` unless your user can write to `/dev/fb0`.

The player prints nothing when it succeeds, and prints one error line and exits with code 1 if it fails.

## How it works

**Maker**
- **Crop:** the video is scaled up just enough to cover the target size, then centre-cropped to exactly `WIDTH x HEIGHT`. A 16:9 video on a 4:3 screen loses its left and right edges. There are no other fit modes.
- **Resolution detection:** the first connected DRM connector's preferred mode, then the framebuffer's mode list, then `xrandr`. It deliberately ignores `/sys/class/graphics/fb0/virtual_size`, which is the size of the whole buffer and can be taller than the visible screen (it once reported 1920x1800). If nothing works it asks you to pass `--res`.
- **Pixel layout:** frames are stored as XRGB8888 (bytes B, G, R, X in memory), copied to the screen as they are.
- **Compression:** each frame is XORed with the previous one and compressed with zlib, with a full keyframe every 30 frames. The format also allows raw and plain-zlib frames, but the maker always writes delta.

**Player** (`player/player.c`, one file, mostly comments)
- Maps the `.bsp` file into memory with `mmap`, so nothing is copied and only the pages it touches are loaded.
- Decodes frames strictly in order into two buffers (current and previous), so memory use does not grow with animation length.
- Draws each frame centred on the screen. If the pack is larger than the screen it is cropped; if smaller, the border is black.
- Waits until a fixed point in time for each frame, so a late frame does not delay all the ones after it.
- Plays once, holds the last frame for 3 seconds (`HOLD_SECONDS`), then exits. There is no loop.

**Framebuffer swap handling**

On a machine with an early firmware framebuffer, `/dev/fb0` can change owner while the animation is playing. On the author's laptop the kernel journal shows:

| Kernel time | Event |
|---|---|
| ~1 s | `simpledrm` provides `fb0`, the early firmware framebuffer |
| ~5.9 s | `amdgpu` replaces it as `fb0` |
| ~7.3 s | GDM, the login screen, takes over |

A player that keeps drawing into its original mapping would stop reaching the screen after the handover. To cope with this, the player tracks which framebuffer it is drawing on:

- `query_screen()` asks the driver which framebuffer `/dev/fb0` currently is.
- `same_screen()` compares its name, memory address and size with the one the player is drawing on.
- `follow_screen()` runs before every frame. If the framebuffer changed, it drops the old mapping, maps the new one, clears it, and tells the caller to redraw the current frame.
- During the final hold, the player checks every 0.1 s (instead of sleeping for 1 s), so a swap after the animation ends still gets the last frame redrawn.
- `open_screen_quietly()` and `close_screen()` open and close the device without exiting. If `/dev/fb0` disappears briefly, the player keeps running and retries. If there is no framebuffer at startup, it still prints one error line and exits with code 1.

This is polling: roughly one extra check per frame plus ten per second during the hold. The cost is negligible, but a swap can leave the screen stale for up to 100 ms during the hold. An event-driven version is listed under Next steps.

## Verified results

- Maker: `boot.mp4` at 1366x768, 24 fps, 3 s gave 72 frames, 302.1 MB raw down to 38.3 MB. `check.py` passed, with pixels within 4 of 255 of an independent ffmpeg extraction (colour-conversion rounding).
- Player, against a plain file standing in for `/dev/fb0`: the final frame matched byte for byte on same-size, larger and smaller screens; a 2 s pack plus the 3 s hold took 5.01 s; truncated, random, empty and missing files each gave one error line; a sanitizer build found no memory errors.
- Player, swap handling, against a fake framebuffer with a simulated driver swap:
  - A swap mid-animation: the new screen received the correct final frame and the old one was no longer written.
  - A swap during the hold: the last frame was redrawn.
  - The framebuffer missing for 1.2 s, then returning: the player stayed alive and redrew.
  - A sanitizer build found no memory errors.
- Player, on real hardware: the animation played correctly from a text console on the author's laptop. This is one machine, so other drivers are untested.
- Fixed blinking cursor on boot animation without switching terminal to graphics mode to avoid being stuck at boot in graphics mode.

## Known limits

- The player needs a 32 bits-per-pixel framebuffer and exits with an error otherwise.
- It never skips frames, so on a slow machine playback runs slow instead of dropping frames.
- A pack is fixed to one resolution. A different screen size gets a cropped or bordered picture, so make one pack per screen.
- It does not run at boot yet; nothing starts it automatically.
- Swap handling has not been tried on real hardware. How `amdgpu` behaves at the handover, including whether there is a visible flicker and how long it lasts, is unknown.
- Swap detection is polling, not event-driven (see Next steps).
- Compression ratio depends on the video. Heavy motion packs much larger than mostly static footage.
- Auto-detect after the DRM-first fix has not been confirmed on real hardware. Passing `--res` always works.

## Project layout

```
README.md
docs/FORMAT.md           byte-level .bsp spec; the contract between maker and player
maker/
├── pyproject.toml
├── check.py             independent pack verification
└── bspack/
    ├── __main__.py      lets you run python3 -m bspack
    ├── cli.py           pack | info | extract
    ├── format.py        .bsp header, writer, reader
    └── frames.py        resolution detection and ffmpeg centre crop
player/
├── Makefile
└── player.c             framebuffer player
```

## Next steps

1. Confirm swap handling on real hardware: rerun `strace -c` on the new build and check what happens when `amdgpu` takes over.
2. Write a systemd service that is active only when the kernel command line contains `bootmotion`, with `Restart=on-failure` as the fallback. Test in QEMU first, and keep Plymouth as a fallback.
3. Add a content check: after drawing, read back a few pixels and redraw if something painted over the picture.
4. Replace the swap polling with kernel events: wait on a `NETLINK_KOBJECT_UEVENT` socket and a `timerfd` with `poll()`, run `follow_screen()` when a graphics uevent arrives, and keep a slow `same_screen()` check as a backstop. Which uevents the `amdgpu` handover emits needs to be checked on real hardware first.
5. Optionally load `amdgpu` early in the initramfs so the handover happens before the animation starts.
6. Optionally switch the display backend to DRM/KMS for tear-free playback.
