# bootsplashd

A Plymouth alternative: a small boot-time player (`daemon/`, coming next) plus a Python maker that builds its animation packs.

## Packer quick start

```bash
cd maker
pip install -e .            # needs ffmpeg on PATH for video/GIF input

bspack pack intro.mp4 --name myboot --res 1920x1080 1366x768 --fit cover --anchor top --fps 24 --duration 3
bspack info out/myboot-1920x1080.bsp
bspack extract out/myboot-1920x1080.bsp      # PNGs to check the crop
bspack verify out/myboot-1920x1080.bsp
```

Without installing: `python3 -m bspack ...` from inside `maker/`.

| Flag | Meaning |
|---|---|
| `--res WxH ...` or `auto` | one pack per resolution; `auto` reads the current display |
| `--fit cover/contain/stretch` | fill+crop, letterbox, or distort |
| `--anchor` | which part survives a crop (`top`, `left`, `bottomright`, ...) |
| `--compress raw/zlib/delta` | delta (default) is smallest |
| `--keyframe N` | keyframe interval for delta mode |

Format spec: `docs/FORMAT.md`.

## Layout

```
maker/bspack/format.py   .bsp header, writer, reader
maker/bspack/frames.py   ffmpeg/Pillow input + crop/fit
maker/bspack/cli.py      pack | info | extract | verify
docs/FORMAT.md            file format contract for the player
```
