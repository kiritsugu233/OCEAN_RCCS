#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)

rank=${SLURM_PROCID:-0}
if ! [[ "$rank" =~ ^[0-9]+$ ]] || (( rank > 253 )); then
    echo "SLURM_PROCID must be an integer between 0 and 253 (got: $rank)." >&2
    exit 1
fi

# Give each job its own multicast group and UDP port by default.
job_id=${SLURM_JOB_ID:-0}
if ! [[ "$job_id" =~ ^[0-9]+$ ]]; then
    job_id=0
fi
group_octet=$((job_id % 250 + 1))
export OCEAN_NET_MODE=${OCEAN_NET_MODE:-socket}
export OCEAN_SOCKET_MCAST=${OCEAN_SOCKET_MCAST:-"239.192.${group_octet}.1:$((20000 + job_id % 20000))"}

if [ -z "${OCEAN_SOCKET_LOCALADDR:-}" ]; then
    OCEAN_SOCKET_LOCALADDR=$(hostname -I 2>/dev/null | awk '{print $1}')
    export OCEAN_SOCKET_LOCALADDR
fi

printf -v mac_suffix '%02x' $((rank + 1))
export VM_MAC=${VM_MAC:-"52:54:00:00:01:${mac_suffix}"}
export LEGOMEM_REGION_ID=${LEGOMEM_REGION_ID:-$((rank + 1))}

echo "Slurm job=${SLURM_JOB_ID:-none} rank=$rank host=$(hostname)"
echo "Rootless VM LAN=${OCEAN_SOCKET_MCAST} localaddr=${OCEAN_SOCKET_LOCALADDR:-auto} mac=$VM_MAC"

exec "$REPO_ROOT/qemu_integration/launch_qemu_legomem.sh" "$@"
