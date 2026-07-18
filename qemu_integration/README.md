# QEMU LegoMem Direct Integration

This directory contains the QEMU-facing LegoMem integration. LegoMem is treated as a memory server. QEMU support is provided by a direct C library that QEMU code can link and call, plus a vendored QEMU `memory-backend-legomem` object for NUMA placement. There is no preload path and no device-model dependency.

## Build

```bash
cd qemu_integration
cmake -S . -B build
cmake --build build -j
ctest --test-dir build --output-on-failure
```

Build outputs:

- `libqemu_legomem.a`: direct QEMU integration library.
- `legomem_server`: simple region-addressed memory server.
- `test_qemu_legomem_api`: API smoke test.

## Direct QEMU Contract

QEMU code should include:

```c
#include "qemu_legomem.h"
```

Then initialize one client per memory backend or address space:

```c
LegoMemQemuClient client;
legomem_qemu_client_init(&client, "127.0.0.1", 9999, 1);
```

Memory backend code can forward operations to the server:

```c
legomem_qemu_read(&client, region_id, offset, buf, len);
legomem_qemu_write(&client, region_id, offset, buf, len);
legomem_qemu_fence(&client, region_id);
legomem_qemu_flush(&client, region_id, offset, len);
```

The address visible to the LegoMem server is:

```text
<region_id, offset>
```

## NUMA Launch Path

`launch_qemu_legomem.sh` launches patched QEMU with a LegoMem NUMA node:

```bash
export LEGOMEM_SERVER_HOST=127.0.0.1
export LEGOMEM_SERVER_PORT=9999
export LEGOMEM_REGION_ID=1
./launch_qemu_legomem.sh
```

The launcher uses:

```text
-object memory-backend-legomem,id=legomem-node1,size=...,server=...,port=...,region-id=...
-numa node,nodeid=1,memdev=legomem-node1
```

The default QEMU binary is `../library/qemu/build/qemu-system-x86_64` when that patched build exists, otherwise `/usr/local/bin/qemu-system-x86_64`.

## Network modes (including rootless Slurm)

`launch_qemu_legomem.sh` supports four network backends selected with
`OCEAN_NET_MODE`:

- `tap` (default): preserves the original deployment and requires an
  administrator-created TAP interface.
- `user`: QEMU/libslirp networking for a single VM. It needs no root privilege;
  use `OCEAN_HOSTFWD=tcp:127.0.0.1:2222-:22` to forward host port 2222 to SSH.
- `socket`: a rootless UDP multicast Ethernet segment suitable for VMs spread
  across nodes in one Slurm allocation. The cluster network must permit UDP
  multicast between the allocated nodes.
- `none`: starts the VM without a guest NIC.

For one VM per Slurm task, launch the wrapper from an allocation:

```bash
srun --nodes=2 --ntasks=2 --ntasks-per-node=1 \
  bash script/launch_qemu_legomem_slurm.sh
```

The wrapper derives a job-specific multicast address, UDP port, MAC address and
LegoMem region ID. Override `OCEAN_SOCKET_MCAST` when required by site policy.
If multicast is disabled, `user` mode remains rootless but does not provide
direct guest-to-guest connectivity; use a site-provided network service or ask
the administrator to pre-create TAP interfaces for multi-VM MPI workloads.

`QEMU_ACCEL=auto` is the default: it uses KVM only when the current user can
read and write `/dev/kvm`, otherwise it falls back to TCG. Set `QEMU_ACCEL=kvm`
to require hardware acceleration or `QEMU_ACCEL=tcg` to force emulation.

## Server

Start the memory server:

```bash
cd qemu_integration/build
./legomem_server 9999
```

The server stores bytes by `region_id:offset` and supports read, write, fence, and flush request types.
