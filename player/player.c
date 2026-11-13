/*
 * player.c - plays a .bsp animation once on the Linux framebuffer, then holds the last frame.
 *
 *   sudo ./player boot.bsp            run from a text console (Ctrl+Alt+F3); Ctrl+C quits
 *
 * How the program flows (see main() at the bottom):
 *
 *   1. load_pack()      open the .bsp file and read its header
 *   2. open_screen()    open /dev/fb0 so we can write pixels straight to the screen
 *   3. for each frame:  decode_frame()  ->  draw_frame()  ->  sleep_until() the next frame is due
 *   4. hold the last frame for a few seconds, then exit
 *
 * The file format is described in docs/FORMAT.md.
 */

#include <fcntl.h>        /* open */
#include <linux/fb.h>     /* framebuffer structs and ioctl numbers */
#include <signal.h>       /* sigaction: catching Ctrl+C */
#include <stdint.h>       /* uint8_t, uint32_t, ... fixed-size integers */
#include <stdio.h>        /* fprintf, perror */
#include <stdlib.h>       /* exit, malloc */
#include <string.h>       /* memcpy, memset, memcmp */
#include <sys/ioctl.h>    /* ioctl: asking a device driver for information */
#include <sys/mman.h>     /* mmap: treat a file or device as plain memory */
#include <sys/stat.h>     /* fstat: file size */
#include <time.h>         /* clock_gettime, clock_nanosleep */
#include <unistd.h>       /* close, sleep */
#include <zlib.h>         /* uncompress */

#define HOLD_SECONDS 3    /* how long the last frame stays on screen */

#define HEADER_SIZE       64
#define INDEX_ENTRY_SIZE  16

/* Values of the "compression" field in the pack header. */
#define COMP_RAW    0     /* frames stored as plain pixels */
#define COMP_ZLIB   1     /* each frame compressed with zlib */
#define COMP_DELTA  2     /* each frame = (frame XOR previous frame), then zlib */


/* ---------- data we keep about the pack and the screen ---------- */

typedef struct {
    const uint8_t *data;        /* the whole .bsp file, mapped into memory */
    size_t         size;        /* file size in bytes */
    uint32_t       width;       /* frame size in pixels */
    uint32_t       height;
    uint32_t       frame_count;
    uint32_t       fps_num;     /* frames per second = fps_num / fps_den */
    uint32_t       fps_den;
    int            compression; /* COMP_RAW, COMP_ZLIB or COMP_DELTA */
    uint64_t       index_offset;/* where the table of frame positions starts */
    size_t         frame_bytes; /* bytes in one decoded frame: width * height * 4 */
} Pack;

typedef struct {
    uint8_t *pixels;            /* memory of the top-left visible pixel */
    uint32_t width;             /* screen size in pixels */
    uint32_t height;
    uint32_t bytes_per_row;     /* may be larger than width * 4 */
} Screen;


/* ---------- small helpers ---------- */

static volatile sig_atomic_t quit_requested;   /* set to 1 when Ctrl+C is pressed */

static void on_signal(int signal_number)
{
    (void)signal_number;
    quit_requested = 1;
}

/* Print an error and stop the program. */
static void die(const char *message)
{
    fprintf(stderr, "player: %s\n", message);
    exit(1);
}

/* Same, but also prints the system's reason (e.g. "Permission denied"). */
static void die_errno(const char *what)
{
    perror(what);
    exit(1);
}

/* The .bsp file stores numbers little-endian (lowest byte first).
 * These turn 2, 4 or 8 bytes in the file into one number. */
static uint16_t read_u16(const uint8_t *p)
{
    return p[0] | (p[1] << 8);
}

static uint32_t read_u32(const uint8_t *p)
{
    return p[0] | (p[1] << 8) | (p[2] << 16) | ((uint32_t)p[3] << 24);
}

static uint64_t read_u64(const uint8_t *p)
{
    uint64_t low  = read_u32(p);
    uint64_t high = read_u32(p + 4);
    return low | (high << 32);
}


/* ---------- 1. the pack file ---------- */

/* Open the .bsp file and fill in the Pack struct from its 64-byte header.
 *
 * Header layout (byte offset: field):
 *    0 magic "BSPK"     8 width        16 fps_num   20 frame_count   28 compression
 *                      12 height       18 fps_den   36 index_offset (8 bytes)
 */
static void load_pack(Pack *pack, const char *path)
{
    int fd = open(path, O_RDONLY);
    if (fd < 0)
        die_errno(path);

    struct stat info;
    if (fstat(fd, &info) < 0)
        die_errno("fstat");
    pack->size = info.st_size;
    if (pack->size < HEADER_SIZE)
        die("not a .bsp file (too small)");

    /* mmap makes the file appear as ordinary memory. Nothing is copied; the kernel loads
     * the parts we touch when we touch them. Once mapped we no longer need the file handle. */
    void *mapped = mmap(NULL, pack->size, PROT_READ, MAP_PRIVATE, fd, 0);
    if (mapped == MAP_FAILED)
        die_errno("mmap pack");
    close(fd);
    pack->data = mapped;

    const uint8_t *header = pack->data;
    if (memcmp(header, "BSPK", 4) != 0)
        die("not a .bsp file (bad magic bytes)");

    pack->width        = read_u32(header + 8);
    pack->height       = read_u32(header + 12);
    pack->fps_num      = read_u16(header + 16);
    pack->fps_den      = read_u16(header + 18);
    pack->frame_count  = read_u32(header + 20);
    pack->compression  = header[28];
    pack->index_offset = read_u64(header + 36);
    pack->frame_bytes  = (size_t)pack->width * pack->height * 4;

    if (pack->width == 0 || pack->height == 0 || pack->width > 16384 || pack->height > 16384)
        die("bad frame size in header");
    if (pack->frame_count == 0 || pack->fps_num == 0 || pack->fps_den == 0)
        die("bad frame count or fps in header");
    if (pack->index_offset + (uint64_t)pack->frame_count * INDEX_ENTRY_SIZE > pack->size)
        die("pack is truncated");
}

/* Decode frame number `frame` into `out` (pack->frame_bytes long).
 * `previous` must be the already-decoded frame before this one (NULL for frame 0);
 * it is only used for delta-compressed packs.
 *
 * Each frame has a 16-byte entry in the index table:
 *    0: where its data starts in the file (8 bytes)   8: data length (4)   12: flags (4)
 * Flag bit 0 means "keyframe": a frame that does not depend on the previous one. */
static void decode_frame(const Pack *pack, uint32_t frame, const uint8_t *previous, uint8_t *out)
{
    const uint8_t *entry = pack->data + pack->index_offset + (uint64_t)frame * INDEX_ENTRY_SIZE;
    uint64_t offset      = read_u64(entry);
    uint32_t size        = read_u32(entry + 8);
    int      is_keyframe = read_u32(entry + 12) & 1;

    if (offset + size > pack->size)
        die("frame data is outside the file");
    const uint8_t *stored = pack->data + offset;

    if (pack->compression == COMP_RAW) {
        if (size < pack->frame_bytes)
            die("raw frame is too short");
        memcpy(out, stored, pack->frame_bytes);
        return;
    }

    /* zlib and delta packs: decompress first. */
    uLongf decoded_size = pack->frame_bytes;
    if (uncompress(out, &decoded_size, stored, size) != Z_OK || decoded_size != pack->frame_bytes)
        die("cannot decompress a frame");

    /* Delta packs: what we just decompressed is only the difference from the previous frame.
     * XOR it with the previous frame to get the real pixels. Keyframes are already complete. */
    if (pack->compression == COMP_DELTA && !is_keyframe) {
        if (previous == NULL)
            die("delta frame has no previous frame to build on");
        for (size_t i = 0; i < pack->frame_bytes; i++)
            out[i] ^= previous[i];
    }
}

/* How many nanoseconds after the start the given frame should appear. */
static uint64_t frame_start_time_ns(const Pack *pack, uint32_t frame)
{
    return (uint64_t)frame * pack->fps_den * 1000000000ULL / pack->fps_num;
}


/* ---------- 2. the screen ---------- */

/* Open the framebuffer device and map its pixel memory so we can write to it directly. */
static void open_screen(Screen *screen, const char *path)
{
    int fd = open(path, O_RDWR);
 
   if (fd < 0)
        die_errno(path);

    /* ioctl asks the display driver questions. */
    struct fb_var_screeninfo var;   /* resolution, bits per pixel, scroll position */
    struct fb_fix_screeninfo fix;   /* bytes per row, total size of the pixel memory */
    if (ioctl(fd, FBIOGET_VSCREENINFO, &var) < 0)
        die_errno("ioctl FBIOGET_VSCREENINFO");
    if (ioctl(fd, FBIOGET_FSCREENINFO, &fix) < 0)
        die_errno("ioctl FBIOGET_FSCREENINFO");

    /* The pack stores 4 bytes per pixel (blue, green, red, unused), so the screen must too. */
    if (var.bits_per_pixel != 32)
        die("screen is not 32 bits per pixel");

    uint8_t *memory = mmap(NULL, fix.smem_len, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (memory == MAP_FAILED)
        die_errno("mmap framebuffer");
    close(fd);

    screen->width         = var.xres;
    screen->height        = var.yres;
    screen->bytes_per_row = fix.line_length;
    screen->pixels        = memory + var.yoffset * fix.line_length + var.xoffset * 4;
}

static void clear_screen(Screen *screen)
{
    for (uint32_t y = 0; y < screen->height; y++)
        memset(screen->pixels + (size_t)y * screen->bytes_per_row, 0, (size_t)screen->width * 4);
}

/* Copy one decoded frame to the middle of the screen.
 * If the frame is bigger than the screen, the edges are cut off.
 * If it is smaller, the border stays black (clear_screen painted it). */
static void draw_frame(Screen *screen, const Pack *pack, const uint8_t *frame)
{
    uint32_t copy_width  = pack->width  < screen->width  ? pack->width  : screen->width;
    uint32_t copy_height = pack->height < screen->height ? pack->height : screen->height;

    uint32_t source_x = (pack->width  - copy_width)  / 2;     /* where to start reading the frame */
    uint32_t source_y = (pack->height - copy_height) / 2;
    uint32_t screen_x = (screen->width  - copy_width)  / 2;   /* where to start writing on screen */
    uint32_t screen_y = (screen->height - copy_height) / 2;

    for (uint32_t row = 0; row < copy_height; row++) {
        const uint8_t *from = frame + ((size_t)(source_y + row) * pack->width + source_x) * 4;
        uint8_t       *to   = screen->pixels + (size_t)(screen_y + row) * screen->bytes_per_row
                              + (size_t)screen_x * 4;
        memcpy(to, from, (size_t)copy_width * 4);
    }
}


/* ---------- 3. timing ---------- */

/* Sleep until `offset_ns` nanoseconds after `start`. Using a fixed point in time (not "sleep
 * 41 ms") means that if one frame is late, the next one waits less and playback catches up. */
static void sleep_until(struct timespec start, uint64_t offset_ns)
{
    struct timespec wake_time = start;
    wake_time.tv_sec  += offset_ns / 1000000000ULL;
    wake_time.tv_nsec += offset_ns % 1000000000ULL;
    if (wake_time.tv_nsec >= 1000000000L) {       /* nanoseconds overflowed into a second */
        wake_time.tv_sec  += 1;
        wake_time.tv_nsec -= 1000000000L;
    }
    clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &wake_time, NULL);
}


/* ---------- main ---------- */

int main(int argc, char **argv)
{
    if (argc < 2) {
        fprintf(stderr, "usage: %s file.bsp [/dev/fb0]\n", argv[0]);
        return 1;
    }
    const char *pack_path   = argv[1];
    const char *screen_path = argc > 2 ? argv[2] : "/dev/fb0";

    Pack   pack;
    Screen screen;
    load_pack(&pack, pack_path);
    open_screen(&screen, screen_path);
    clear_screen(&screen);

    /* Ctrl+C or `kill` sets quit_requested instead of killing the program mid-frame.
     * SA_RESTART is not set, so a sleeping program wakes up right away. */
    struct sigaction action = { .sa_handler = on_signal };
    sigaction(SIGINT, &action, NULL);
    sigaction(SIGTERM, &action, NULL);

    /* Two frame buffers: the frame being built, and the one before it (needed for delta).
     * Even frames use buffers[0], odd frames use buffers[1], so they swap every frame. */
    uint8_t *buffers[2] = { malloc(pack.frame_bytes), malloc(pack.frame_bytes) };
    if (buffers[0] == NULL || buffers[1] == NULL)
        die("out of memory");

    struct timespec start;
    clock_gettime(CLOCK_MONOTONIC, &start);

    for (uint32_t frame = 0; frame < pack.frame_count && !quit_requested; frame++) {
        uint8_t *current  = buffers[frame % 2];
        uint8_t *previous = (frame == 0) ? NULL : buffers[(frame + 1) % 2];

        decode_frame(&pack, frame, previous, current);
        draw_frame(&screen, &pack, current);
        sleep_until(start, frame_start_time_ns(&pack, frame + 1));
    }

    /* Leave the last frame on screen for a moment (cut short by Ctrl+C). */
    if (!quit_requested)
        sleep(HOLD_SECONDS);

    return 0;
}
