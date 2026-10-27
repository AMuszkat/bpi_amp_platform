#!/bin/bash
# Setup script for Linux VM to mount macOS shared folder
# Run this once in your Linux VM to mount the juce_plugins/ folder shared by the host
# (UTM: VM Settings -> Sharing -> Directory Sharing). Used by tools/build-linux-vm.sh.

set -e

MOUNT_POINT="/mnt/macos"
FSTAB_ENTRY="share $MOUNT_POINT 9p trans=virtio,version=9p2000.L,rw,_netdev 0 0"

echo "=== Linux VM Setup for macOS Shared Folder ==="
echo ""

# Check if already mounted
if mountpoint -q "$MOUNT_POINT" 2>/dev/null; then
    echo "[OK] $MOUNT_POINT is already mounted"
    echo "     Contents:"
    ls "$MOUNT_POINT" | head -5
    echo ""
else
    # Create mount point if it doesn't exist
    if [ ! -d "$MOUNT_POINT" ]; then
        echo "[*] Creating mount point $MOUNT_POINT..."
        sudo mkdir -p "$MOUNT_POINT"
        echo "    Done"
    else
        echo "[OK] Mount point $MOUNT_POINT already exists"
    fi

    # Try to mount
    echo "[*] Mounting shared folder..."
    if sudo mount -t 9p -o trans=virtio,version=9p2000.L,rw share "$MOUNT_POINT"; then
        echo "    Done"
        echo "    Contents:"
        ls "$MOUNT_POINT" | head -5
    else
        echo ""
        echo "[ERROR] Mount failed. Please check:"
        echo "  1. UTM: VM Settings → Sharing → Enable Directory Sharing"
        echo "  2. The juce_plugins folder is shared"
        echo "  3. The VM was restarted after enabling sharing"
        exit 1
    fi
fi

echo ""

# Check if fstab entry exists
if grep -q "share.*$MOUNT_POINT.*9p" /etc/fstab 2>/dev/null; then
    echo "[OK] Auto-mount entry already in /etc/fstab"
else
    echo "[*] Adding auto-mount entry to /etc/fstab..."
    echo "$FSTAB_ENTRY" | sudo tee -a /etc/fstab > /dev/null
    echo "    Done"
    echo "    Entry: $FSTAB_ENTRY"
fi

echo ""
echo "=== Setup complete ==="
echo ""
echo "The shared folder is mounted at: $MOUNT_POINT"
echo "It will auto-mount on boot."
echo ""
echo "Next steps:"
echo "  1. Install build dependencies (see the header of tools/build-linux-vm.sh)."
echo "  2. Build a plugin (JUCE is installed on the first run):"
echo "       $MOUNT_POINT/tools/build-linux-vm.sh <plugin|all>"
