"""
.bsp file format (version 1). Little-endian. See docs/FORMAT.md.

  [ header: 64 bytes ][ frame blobs ... ][ index: frame_count * 16 bytes ]
"""
import struct
import zlib
from dataclasses import dataclass

MAGIC = b"BSPK"
VERSION = 1
HEADER_FMT = "<4sHHIIHHI4sB3xIQ20x"
HEADER_SIZE = struct.calcsize(HEADER_FMT)      # 64
INDEX_FMT = "<QII"                              # offset, size, flags
INDEX_SIZE = struct.calcsize(INDEX_FMT)         # 16

PIXFMT_XRGB8888 = b"XR24"   # bytes in memory per pixel: B, G, R, X (matches DRM XRGB8888)
FLAG_LOOP = 1
COMP_RAW, COMP_ZLIB, COMP_DELTA_ZLIB = 0, 1, 2
COMP_NAMES = {COMP_RAW: "raw", COMP_ZLIB: "zlib", COMP_DELTA_ZLIB: "delta+zlib"}
IDX_KEYFRAME = 1


@dataclass
class Header:
    width: int
    height: int
    fps_num: int
    fps_den: int
    frame_count: int = 0
    compression: int = COMP_DELTA_ZLIB
    bg: int = 0x000000          # 0xRRGGBB background colour
    flags: int = FLAG_LOOP
    index_offset: int = 0
    pixfmt: bytes = PIXFMT_XRGB8888
    version: int = VERSION

    def pack(self) -> bytes:
        return struct.pack(HEADER_FMT, MAGIC, self.version, self.flags, self.width,
                           self.height, self.fps_num, self.fps_den, self.frame_count,
                           self.pixfmt, self.compression, self.bg, self.index_offset)

    @classmethod
    def unpack(cls, data: bytes) -> "Header":
        (magic, ver, flags, w, h, fn, fd, count, pixfmt, comp, bg, idx) = \
            struct.unpack(HEADER_FMT, data[:HEADER_SIZE])
        if magic != MAGIC:
            raise ValueError("not a .bsp file (bad magic)")
        if ver != VERSION:
            raise ValueError(f"unsupported version {ver}")
        return cls(w, h, fn, fd, count, comp, bg, flags, idx, pixfmt, ver)

    @property
    def fps(self) -> float:
        return self.fps_num / self.fps_den

    @property
    def frame_bytes(self) -> int:
        return self.width * self.height * 4


def xor_bytes(a: bytes, b: bytes) -> bytes:
    n = len(a)
    return (int.from_bytes(a, "little") ^ int.from_bytes(b, "little")).to_bytes(n, "little")


class PackWriter:
    """Streaming writer: frames are compressed and written as they arrive."""

    def __init__(self, path, header: Header, keyframe_interval=30, level=6):
        self.h = header
        self.kf = max(1, keyframe_interval)
        self.level = level
        self.f = open(path, "wb")
        self.f.write(b"\0" * HEADER_SIZE)       # placeholder, rewritten on close
        self.index = []
        self.prev = None
        self.raw_total = 0

    def add_frame(self, raw: bytes):
        if len(raw) != self.h.frame_bytes:
            raise ValueError("frame has wrong size")
        n = len(self.index)
        is_key = (n % self.kf == 0) or self.h.compression != COMP_DELTA_ZLIB
        comp = self.h.compression
        if comp == COMP_RAW:
            blob = raw
        elif comp == COMP_ZLIB:
            blob = zlib.compress(raw, self.level)
        else:
            payload = raw if is_key else xor_bytes(raw, self.prev)
            blob = zlib.compress(payload, self.level)
        self.prev = raw
        self.index.append((self.f.tell(), len(blob), IDX_KEYFRAME if is_key else 0))
        self.f.write(blob)
        self.raw_total += len(raw)

    def close(self):
        self.h.index_offset = self.f.tell()
        self.h.frame_count = len(self.index)
        for off, size, flags in self.index:
            self.f.write(struct.pack(INDEX_FMT, off, size, flags))
        self.f.seek(0)
        self.f.write(self.h.pack())
        total = self.f.seek(0, 2)
        self.f.close()
        return total


class PackReader:
    def __init__(self, path):
        self.f = open(path, "rb")
        self.h = Header.unpack(self.f.read(HEADER_SIZE))
        self.f.seek(self.h.index_offset)
        data = self.f.read(self.h.frame_count * INDEX_SIZE)
        self.index = [struct.unpack_from(INDEX_FMT, data, i * INDEX_SIZE)
                      for i in range(self.h.frame_count)]

    def _blob(self, i):
        off, size, _ = self.index[i]
        self.f.seek(off)
        return self.f.read(size)

    def frame(self, i) -> bytes:
        """Return raw BGRX bytes of frame i (decodes from the nearest keyframe)."""
        comp = self.h.compression
        if comp == COMP_RAW:
            return self._blob(i)
        if comp == COMP_ZLIB:
            return zlib.decompress(self._blob(i))
        k = i
        while not (self.index[k][2] & IDX_KEYFRAME):
            k -= 1
        cur = zlib.decompress(self._blob(k))
        for j in range(k + 1, i + 1):
            cur = xor_bytes(zlib.decompress(self._blob(j)), cur)
        return cur
