# .bsp format, version 1

All integers little-endian. This is the contract between the Python maker and the boot-time player.

```
offset 0   : header (64 bytes)
offset 64  : frame blobs, back to back
index_offset : index table, frame_count * 16 bytes
```

## Header (64 bytes)

| Off | Size | Field        | Notes |
|----:|-----:|--------------|-------|
| 0   | 4    | magic        | `BSPK` |
| 4   | 2    | version      | 1 |
| 6   | 2    | flags        | bit0 = loop |
| 8   | 4    | width        | pixels |
| 12  | 4    | height       | pixels |
| 16  | 2    | fps_num      | |
| 18  | 2    | fps_den      | fps = num/den |
| 20  | 4    | frame_count  | |
| 24  | 4    | pixel_format | `XR24`: memory bytes per pixel are B,G,R,X (DRM XRGB8888) |
| 28  | 1    | compression  | 0 raw, 1 zlib, 2 delta+zlib |
| 29  | 3    | reserved     | 0 |
| 32  | 4    | bg_color     | 0x00RRGGBB, fill colour around the frame |
| 36  | 8    | index_offset | absolute file offset of the index |
| 44  | 20   | reserved     | 0 |

## Index entry (16 bytes each)

| Size | Field  | Notes |
|-----:|--------|-------|
| 8    | offset | absolute file offset of the blob |
| 4    | size   | blob length in bytes |
| 4    | flags  | bit0 = keyframe |

## Decoding a frame (frame buffer = width*height*4 bytes)

- **raw**: blob is the pixels.
- **zlib**: `inflate(blob)` is the pixels.
- **delta+zlib**: `d = inflate(blob)`. If keyframe, pixels = d. Otherwise pixels = d XOR previous_frame_pixels.
  Frame 0 is always a keyframe, so looping back to 0 needs no history.

Player memory need: 2 frame buffers (current + previous) plus the compressed blob, independent of animation length.

## Player behaviour (recommended)

- If display mode != pack size: centre the frame, fill the rest with bg_color (or pick another pack).
- Frame to show = floor(elapsed * fps_num / fps_den); skip frames when behind. Keep delta state correct by still decoding skipped delta frames.
- Loop only if flags bit0 is set; otherwise hold the last frame.
