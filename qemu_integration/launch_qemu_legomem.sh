#!/bin/bash

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
DEFAULT_QEMU="$SCRIPT_DIR/../library/qemu/build/qemu-system-x86_64"

if [ -x "$DEFAULT_QEMU" ]; then
    QEMU_BINARY=${QEMU_BINARY:-"$DEFAULT_QEMU"}
else
    QEMU_BINARY=${QEMU_BINARY:-/usr/local/bin/qemu-system-x86_64}
fi

KERNEL_IMAGE=${KERNEL_IMAGE:-./bzImage}
DISK_IMAGE=${DISK_IMAGE:-./qemu.img}
VM_BASE_MEMORY=${VM_BASE_MEMORY:-4G}
LEGOMEM_NODE_SIZE=${LEGOMEM_NODE_SIZE:-1G}
VM_TOTAL_MEMORY=${VM_TOTAL_MEMORY:-5G}
VM_MAX_MEMORY=${VM_MAX_MEMORY:-16G}
QEMU_ACCEL=${QEMU_ACCEL:-auto}
OCEAN_NET_MODE=${OCEAN_NET_MODE:-tap}
OCEAN_MEMORY_MODE=${OCEAN_MEMORY_MODE:-legomem-numa}
TAP_IFACE=${TAP_IFACE:-tap0}
VM_MAC=${VM_MAC:-52:54:00:00:00:01}
export LEGOMEM_SERVER_HOST=${LEGOMEM_SERVER_HOST:-127.0.0.1}
export LEGOMEM_SERVER_PORT=${LEGOMEM_SERVER_PORT:-9999}
export LEGOMEM_REGION_ID=${LEGOMEM_REGION_ID:-1}

echo "Starting QEMU with LegoMem NUMA node"
echo "  QEMU binary: ${QEMU_BINARY}"
echo "  LegoMem server: ${LEGOMEM_SERVER_HOST}:${LEGOMEM_SERVER_PORT}"
echo "  LegoMem region: ${LEGOMEM_REGION_ID}"
echo "  NUMA size: ${LEGOMEM_NODE_SIZE}"
echo "  Guest memory mode: ${OCEAN_MEMORY_MODE}"

machine_args=()
memory_args=()
kernel_append="root=/dev/vda rw console=ttyS0,115200 nokaslr"

case "$OCEAN_MEMORY_MODE" in
    legomem-numa)
        machine_args=(-machine q35)
        memory_args=(
            -m "$VM_TOTAL_MEMORY,slots=8,maxmem=$VM_MAX_MEMORY"
            -object "memory-backend-ram,id=ram-node0,size=$VM_BASE_MEMORY"
            -numa node,nodeid=0,cpus=0-3,memdev=ram-node0
            -object "memory-backend-legomem,id=legomem-node1,size=$LEGOMEM_NODE_SIZE,server=$LEGOMEM_SERVER_HOST,port=$LEGOMEM_SERVER_PORT,region-id=$LEGOMEM_REGION_ID"
            -numa node,nodeid=1,memdev=legomem-node1
        )
        ;;
    cxl)
        CXL_MEMORY=${CXL_MEMORY:-$LEGOMEM_NODE_SIZE}
        CXL_LSA_SIZE=${CXL_LSA_SIZE:-2M}
        CXL_HOST_ID=${CXL_HOST_ID:-${SLURM_PROCID:-0}}
        CXL_RUNTIME_DIR=${CXL_RUNTIME_DIR:-/dev/shm/ocean-cxl-${SLURM_JOB_ID:-manual}-${CXL_HOST_ID}}
        CXL_BACKING_PATH=${CXL_BACKING_PATH:-$CXL_RUNTIME_DIR/cxl-mem.raw}
        CXL_LSA_PATH=${CXL_LSA_PATH:-$CXL_RUNTIME_DIR/cxl-lsa.raw}

        mkdir -p "$CXL_RUNTIME_DIR"
        truncate -s "$CXL_MEMORY" "$CXL_BACKING_PATH"
        truncate -s "$CXL_LSA_SIZE" "$CXL_LSA_PATH"

        machine_args=(-machine q35,cxl=on)
        memory_args=(
            -m "$VM_BASE_MEMORY,slots=8,maxmem=$VM_MAX_MEMORY"
            -object "memory-backend-ram,id=ram-node0,size=$VM_BASE_MEMORY"
            -numa node,nodeid=0,cpus=0-3,memdev=ram-node0
            -object "memory-backend-file,id=cxl-mem1,share=on,mem-path=$CXL_BACKING_PATH,size=$CXL_MEMORY"
            -object "memory-backend-file,id=cxl-lsa1,share=on,mem-path=$CXL_LSA_PATH,size=$CXL_LSA_SIZE"
            -device pxb-cxl,bus_nr=52,bus=pcie.0,id=cxl.1
            -device cxl-rp,port=0,bus=cxl.1,id=cxl-rp0,chassis=0,slot=0
            -device cxl-type3,bus=cxl-rp0,persistent-memdev=cxl-mem1,lsa=cxl-lsa1,id=cxl-pmem0,sn=0x1
            -M "cxl-fmw.0.targets.0=cxl.1,cxl-fmw.0.size=$CXL_MEMORY"
        )
        kernel_append+=" cxl_region_size=$CXL_MEMORY"

        echo "  CXL backing: ${CXL_BACKING_PATH}"
        echo "  CXL LSA: ${CXL_LSA_PATH}"
        ;;
    *)
        echo "Unsupported OCEAN_MEMORY_MODE=$OCEAN_MEMORY_MODE (expected legomem-numa or cxl)." >&2
        exit 1
        ;;
esac

net_args=()
case "$OCEAN_NET_MODE" in
    tap)
        if [ ! -e "/sys/class/net/$TAP_IFACE" ]; then
            echo "TAP interface not found: $TAP_IFACE" >&2
            echo "On an unprivileged Slurm node, use OCEAN_NET_MODE=user or OCEAN_NET_MODE=socket." >&2
            exit 1
        fi
        net_args=(-netdev "tap,id=net0,ifname=$TAP_IFACE,script=no,downscript=no"
                  -device "virtio-net-pci,netdev=net0,mac=$VM_MAC,bus=pcie.0")
        ;;
    user)
        # libslirp runs in the QEMU process and needs no TAP device or
        # CAP_NET_ADMIN. OCEAN_HOSTFWD may contain a QEMU hostfwd rule such as
        # tcp:127.0.0.1:2222-:22.
        user_netdev="user,id=net0"
        if [ -n "${OCEAN_HOSTFWD:-}" ]; then
            user_netdev+=",hostfwd=${OCEAN_HOSTFWD}"
        fi
        net_args=(-netdev "$user_netdev"
                  -device "virtio-net-pci,netdev=net0,mac=$VM_MAC,bus=pcie.0")
        ;;
    socket)
        # QEMU's UDP multicast socket backend gives VMs on allocated Slurm
        # nodes one shared Ethernet segment without creating host interfaces.
        OCEAN_SOCKET_MCAST=${OCEAN_SOCKET_MCAST:-230.0.0.1:1234}
        socket_netdev="socket,id=net0,mcast=${OCEAN_SOCKET_MCAST}"
        if [ -n "${OCEAN_SOCKET_LOCALADDR:-}" ]; then
            socket_netdev+=",localaddr=${OCEAN_SOCKET_LOCALADDR}"
        fi
        net_args=(-netdev "$socket_netdev"
                  -device "virtio-net-pci,netdev=net0,mac=$VM_MAC,bus=pcie.0")
        ;;
    none)
        net_args=()
        ;;
    *)
        echo "Unsupported OCEAN_NET_MODE=$OCEAN_NET_MODE (expected tap, user, socket, or none)." >&2
        exit 1
        ;;
esac

echo "  Network mode: ${OCEAN_NET_MODE}"

case "$QEMU_ACCEL" in
    auto)
        if [ -r /dev/kvm ] && [ -w /dev/kvm ]; then
            accel_args=(--enable-kvm -cpu qemu64,+xsave,+rdtscp,+avx,+avx2,+sse4.1,+sse4.2,+clflushopt)
            selected_accel=kvm
        else
            accel_args=(-accel tcg,thread=multi -cpu qemu64)
            selected_accel=tcg
        fi
        ;;
    kvm)
        if [ ! -r /dev/kvm ] || [ ! -w /dev/kvm ]; then
            echo "QEMU_ACCEL=kvm requested, but this user cannot access /dev/kvm." >&2
            exit 1
        fi
        accel_args=(--enable-kvm -cpu qemu64,+xsave,+rdtscp,+avx,+avx2,+sse4.1,+sse4.2,+clflushopt)
        selected_accel=kvm
        ;;
    tcg)
        accel_args=(-accel tcg,thread=multi -cpu qemu64)
        selected_accel=tcg
        ;;
    *)
        echo "Unsupported QEMU_ACCEL=$QEMU_ACCEL (expected auto, kvm, or tcg)." >&2
        exit 1
        ;;
esac

echo "  Accelerator: ${selected_accel}"

exec "$QEMU_BINARY" \
    "${accel_args[@]}" \
    -smp 4 \
    "${machine_args[@]}" \
    "${memory_args[@]}" \
    -kernel "$KERNEL_IMAGE" \
    -append "$kernel_append" \
    -drive file="$DISK_IMAGE",if=virtio,format=raw \
    "${net_args[@]}" \
    -fsdev local,security_model=none,id=fsdev0,path=/dev/shm \
    -device virtio-9p-pci,id=fs0,fsdev=fsdev0,mount_tag=hostshm,bus=pcie.0 \
    -nographic \
    "$@"
