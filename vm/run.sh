#!/bin/bash
cd "$(dirname "$0")"
exec qemu-system-x86_64 -m 1G -machine q35,accel=kvm:tcg -vga std \
  -drive if=pflash,format=raw,unit=0,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.fd \
  -drive if=pflash,format=raw,unit=1,file=vars.fd \
  -kernel kernel -initrd initrd.gz -append "console=tty0 console=ttyS0 rdinit=/init" \
  -serial stdio
