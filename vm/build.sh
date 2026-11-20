#!/bin/bash
# Builds a tiny initramfs (busybox + player + one pack) for testing in QEMU.
set -e
cd "$(dirname "$0")"
PACK=../maker/out/vm-1024x768.bsp
fail() { echo; echo "FAILED: $1"; exit 1; }
step() { echo; echo "== $1"; }

step "1/5 tools"
for t in qemu-system-x86_64 busybox cpio gzip cc; do
    command -v "$t" >/dev/null || fail "'$t' is not installed (sudo apt install qemu-system-x86 busybox-static cpio ovmf build-essential zlib1g-dev)"
done
ldd "$(command -v busybox)" 2>&1 | grep -q "not a dynamic" || fail "busybox is not the static build (sudo apt install busybox-static)"
ls /usr/share/OVMF/OVMF_CODE_4M.fd /usr/share/OVMF/OVMF_VARS_4M.fd >/dev/null 2>&1 || fail "UEFI firmware not found; run: ls /usr/share/OVMF/"
echo "ok"

step "2/5 pack"
[ -f "$PACK" ] || fail "$PACK is missing (make it with: cd ../maker && python3 -m bspack pack boot.mp4 --name vm --res 1024x768 --fps 24 --duration 3)"
ls -l "$PACK"

step "3/5 static player"
cc -O2 -static -o player-static ../player/player.c -lz || fail "static build failed (is libc6-dev / zlib1g-dev installed? static zlib needs zlib1g-dev)"
./player-static 2>&1 | head -1

step "4/5 initramfs"
rm -rf root
mkdir -p root/bin root/dev root/proc root/sys root/pack
cp "$(command -v busybox)" root/bin/busybox
cp player-static root/bin/player
cp "$PACK" root/pack/boot.bsp
sudo mknod -m 600 root/dev/console c 5 1
sudo mknod -m 666 root/dev/null c 1 3
cat > root/init <<'INIT'
#!/bin/busybox sh
/bin/busybox --install -s /bin
mount -t proc none /proc
mount -t sysfs none /sys
mount -t devtmpfs none /dev
echo "== framebuffer check =="
ls -l /dev/fb* 2>&1
cat /proc/fb 2>&1
cat /sys/class/graphics/fb0/virtual_size /sys/class/graphics/fb0/modes 2>&1
dmesg | grep -i -E "fb|drm|efifb|simpledrm" | head -20
echo "== running player =="
/bin/player --hold 3 --timeout 20 /pack/boot.bsp
echo "player exit code: $?"
sleep 5
poweroff -f
INIT
chmod +x root/init
(cd root && find . | cpio -o -H newc -R 0:0 2>/dev/null | gzip) > initrd.gz
[ -s initrd.gz ] || fail "initrd.gz is empty"
ls -l initrd.gz

step "5/5 kernel and firmware vars"
sudo cp "/boot/vmlinuz-$(uname -r)" kernel
sudo chown "$USER" kernel
cp /usr/share/OVMF/OVMF_VARS_4M.fd vars.fd
ls -l kernel vars.fd

echo; echo "ALL DONE. Now run ./run.sh"
