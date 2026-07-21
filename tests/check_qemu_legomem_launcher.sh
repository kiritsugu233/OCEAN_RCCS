#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
LAUNCHER="$ROOT_DIR/qemu_integration/launch_qemu_legomem.sh"

if ! grep -q "root=/dev/vda" "$LAUNCHER"; then
    echo "launcher must use the explicit virtio root disk path /dev/vda" >&2
    exit 1
fi

if ! grep -q 'if=virtio' "$LAUNCHER"; then
    echo "launcher must attach qemu.img as an explicit virtio disk" >&2
    exit 1
fi

if ! grep -q 'OCEAN_MEMORY_MODE' "$LAUNCHER"; then
    echo "launcher must expose the guest memory mode selector" >&2
    exit 1
fi

if ! grep -q 'cxl-type3' "$LAUNCHER"; then
    echo "launcher cxl mode must expose a CXL Type-3 device" >&2
    exit 1
fi
