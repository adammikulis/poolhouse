# The native column of the speed table: the guest's own system and workloads under chroot on a debug emulator's
# processor, with no virtual machine. Run as root on an emulator only:
#   adb -s emulator-NNNN push system.img bench.sh native.sh /data/local/tmp/iso/
#   adb -s emulator-NNNN shell su 0 sh /data/local/tmp/iso/native.sh /data/local/tmp/iso
set -eu
folder=$1
root=$folder/root
mkdir -p "$root"
tar -x -f "$folder/system.img" -C "$root"
mkdir -p "$root/proc" "$root/sys" "$root/dev" "$root/tmp" "$root/root"
mount -t proc proc "$root/proc"
mount -o bind /dev "$root/dev"
trap 'umount "$root/dev" "$root/proc"' EXIT
chmod 1777 "$root/tmp"
cp "$folder/bench.sh" "$root/tmp/task.sh"
chroot "$root" /bin/busybox --install -s
chroot "$root" /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin HOME=/root /bin/sh /tmp/task.sh
