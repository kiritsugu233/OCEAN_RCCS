# RCCS Phase 1: legacy CXLMemSim TCP baseline

This workflow establishes a reproducible, rootless, single-node baseline for
the legacy `dev-cxlmemsim` implementation on the RCCS Slurm cluster.

The two source copies have different roles:

- Mac:
  `/Users/guanghongxu/rccs-projects/OCEAN_fam_emu_cxlmemsim`
  is the source of truth for agent analysis and source changes.
- RCCS:
  `/home/users/u0001928/OCEAN_CXLMEMSIM`
  is used only for builds and runs.
- RCCS login:
  `u0001928@login.cloud.r-ccs.riken.jp`

Source changes travel from Mac to RCCS as a Git bundle copied with `scp`.
Logs travel back from RCCS to Mac as a compressed archive. No GitHub push is
needed.

## 1. Scope and success criteria

Phase 1 uses:

- outer repository branch `dev-cxlmemsim`;
- pinned base commit `4e4fc38`;
- the top-level full `cxlmemsim_server`, not the lightweight
  `qemu_integration/cxlmemsim_server`;
- the vendored QEMU under `library/qemu`;
- `CXL_TRANSPORT_MODE=tcp`;
- one Genoa compute node;
- KVM;
- rootless QEMU `user` networking;
- the complete author-provided kernel and approximately 26 GB disk image;
- guest `/dev/dax0.0` in `devdax` mode.

Phase 1 does not use:

- TAP, Linux bridge, `sudo`, or host root;
- PGAS-SHM;
- `memory-backend-legomem`;
- `daxctl reconfigure-device --mode=system-ram`;
- `numactl --membind=1` as the main acceptance test;
- two-node execution.

Phase 1 passes when:

1. the full server starts in TCP mode;
2. QEMU reports the TCP transport and connects to the server;
3. the guest creates `/dev/dax0.0`;
4. `daxctl list` reports `devdax`;
5. a small aligned `mmap()` write/read test on `/dev/dax0.0` passes;
6. neither QEMU nor the server reports SHM/PGAS timeout errors.

## 2. One-time Mac setup

The local comparison clone is already pinned at `4e4fc38`. Use a dedicated
local branch:

```bash
cd /Users/guanghongxu/rccs-projects/OCEAN_fam_emu_cxlmemsim

git switch rccs/phase1-cxlmemsim-tcp
git status --short --branch
git rev-parse --short HEAD
```

Expected initial base:

```text
4e4fc38
```

Do not push this branch to `fam-emu/OCEAN`.

## 3. One-time RCCS clone

Run from the Mac:

```bash
ssh u0001928@login.cloud.r-ccs.riken.jp
```

Run on the RCCS login node:

```bash
cd /home/users/u0001928

git clone \
  --branch dev-cxlmemsim \
  --single-branch \
  https://github.com/fam-emu/OCEAN.git \
  OCEAN_CXLMEMSIM

cd /home/users/u0001928/OCEAN_CXLMEMSIM

git switch -c rccs/phase1-cxlmemsim-tcp 4e4fc38

git status --short --branch
git rev-parse --short HEAD
```

Do not run the repository host-setup scripts. They use `sudo`, install host
packages, configure TAP/bridges, and mix in the `submodules/CXLMemSim`
implementation. For this baseline, the outer repository is the only source
tree.

## 4. Reuse the existing VM assets

Do not copy the 26 GB image into Git or transfer it from the Mac. Reuse the
assets already stored on the RCCS shared filesystem:

```bash
KERNEL=/home/users/u0001928/OCEAN_RCCS/assets/author-20260720/bzImage
DISK=/home/users/u0001928/OCEAN_RCCS/assets/author-20260720/qemu.img

test -s "$KERNEL" &&
  echo "KERNEL=PASS" ||
  echo "KERNEL=FAIL"

test -s "$DISK" &&
  echo "DISK=PASS" ||
  echo "DISK=FAIL"

sha256sum "$KERNEL" "$DISK"
```

Expected hashes:

```text
5c65f321d0a7623f9a5c415edf33146834229ce42175b62fe2774191d1c8f7ef  bzImage
74352a0fb77c5e1421d128e4a9d5ec7c7d67ed61be73de48222533669590021d  qemu.img
```

Always launch the disk with QEMU `-snapshot`, so Phase 1 cannot alter the
shared base image.

## 5. Normal Mac-to-RCCS source synchronization

### 5.1 Commit locally

After the local agent makes a source change:

```bash
cd /Users/guanghongxu/rccs-projects/OCEAN_fam_emu_cxlmemsim

git status --short
git diff --check
git diff

git add <only-the-files-for-this-change>
git commit -m "phase1: describe the change"
```

Do not add build directories, VM images, core dumps, or logs.

### 5.2 Create an incremental bundle

```bash
cd /Users/guanghongxu/rccs-projects/OCEAN_fam_emu_cxlmemsim

BUNDLE=/Users/guanghongxu/rccs-projects/phase1-cxlmemsim-tcp.bundle

git bundle create \
  "$BUNDLE" \
  rccs/phase1-cxlmemsim-tcp \
  ^dev-cxlmemsim

git bundle verify "$BUNDLE"
ls -lh "$BUNDLE"
```

The negative `^dev-cxlmemsim` revision keeps the bundle incremental instead of
copying the entire vendored QEMU history.

### 5.3 Copy the bundle

Run from the Mac:

```bash
scp \
  /Users/guanghongxu/rccs-projects/phase1-cxlmemsim-tcp.bundle \
  u0001928@login.cloud.r-ccs.riken.jp:/home/users/u0001928/
```

### 5.4 Apply it on RCCS

Run on the RCCS login node:

```bash
cd /home/users/u0001928/OCEAN_CXLMEMSIM

git status --short
```

The output must be empty before continuing. Then:

```bash
git fetch \
  /home/users/u0001928/phase1-cxlmemsim-tcp.bundle \
  rccs/phase1-cxlmemsim-tcp:refs/remotes/local/phase1-cxlmemsim-tcp

git merge \
  --ff-only \
  refs/remotes/local/phase1-cxlmemsim-tcp

git status --short --branch
git log --oneline --decorate -5
```

If `--ff-only` refuses, stop and transfer the status/log output back for
analysis. Do not resolve it by resetting the RCCS tree.

## 6. Allocate one Genoa node

Phase 1 needs only one node. The exact allocation command depends on RCCS
account/QoS policy. After obtaining a running allocation, record:

```bash
echo "JOB=$SLURM_JOB_ID"
hostname
nproc
ls -l /dev/kvm
```

All builds and QEMU runs should happen inside the allocated compute node, not
on the login node.

If entering an already running allocation from the login node:

```bash
srun \
  --jobid=<JOBID> \
  --overlap \
  --nodes=1 \
  --ntasks=1 \
  --pty \
  bash -l
```

## 7. Activate the existing build environment

Run on the allocated Genoa compute node:

```bash
ENV=/hs/work0/home/users/u0001928/micromamba/envs/ocean-build

export PATH="$ENV/bin:/usr/bin:/bin"
export LD_LIBRARY_PATH="$ENV/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PKG_CONFIG_PATH="$ENV/lib/pkgconfig:$ENV/share/pkgconfig"

command -v python
command -v cmake
command -v meson
command -v ninja
command -v pkg-config
command -v gcc
command -v g++

python --version
cmake --version | head -1
meson --version
ninja --version
gcc --version | head -1
pkg-config --modversion glib-2.0
pkg-config --modversion pixman-1
pkg-config --modversion slirp
```

This uses the environment binaries directly and avoids concurrent
`micromamba run` lock contention across Slurm tasks.

## 8. Create a run directory and record provenance

```bash
REPO=/home/users/u0001928/OCEAN_CXLMEMSIM
RUN_ID=$(date +%Y%m%d-%H%M%S)-${SLURM_JOB_ID:-manual}
RUN_DIR=/home/users/u0001928/ocean-phase1-logs/$RUN_ID

mkdir -p "$RUN_DIR"
cd "$REPO"

git status --short --branch >"$RUN_DIR/git-status.txt"
git log -1 --format=fuller >"$RUN_DIR/git-head.txt"

hostname >"$RUN_DIR/hostname.txt"
env | sort >"$RUN_DIR/environment.txt"

echo "RUN_DIR=$RUN_DIR"
```

Use this same `RUN_DIR` for server, build, QEMU, and guest logs.

## 9. Build the full legacy server

Build the top-level target. Do not use the similarly named lightweight server
under `qemu_integration`.

```bash
cd /home/users/u0001928/OCEAN_CXLMEMSIM

cmake \
  -S . \
  -B build-rccs-phase1-server \
  -G Ninja \
  -DCMAKE_BUILD_TYPE=RelWithDebInfo \
  2>&1 |
  tee "$RUN_DIR/server-configure.log"

cmake \
  --build build-rccs-phase1-server \
  --target cxlmemsim_server \
  -j 8 \
  2>&1 |
  tee "$RUN_DIR/server-build.log"

SERVER=/home/users/u0001928/OCEAN_CXLMEMSIM/build-rccs-phase1-server/cxlmemsim_server

test -x "$SERVER" &&
  echo "SERVER_BUILD=PASS" ||
  echo "SERVER_BUILD=FAIL"

"$SERVER" --help |
  head -40 |
  tee "$RUN_DIR/server-help.txt"
```

If this fails on a missing header or compiler error, stop. Send
`server-configure.log` and `server-build.log` back to the Mac; do not install
random packages or edit tracked source on the compute node.

## 10. Build the vendored legacy QEMU

```bash
cd /home/users/u0001928/OCEAN_CXLMEMSIM/library/qemu

mkdir -p build-rccs-phase1
cd build-rccs-phase1

../configure \
  --target-list=x86_64-softmmu \
  --enable-kvm \
  --enable-slirp \
  --disable-u2f \
  --disable-libudev \
  --disable-libdw \
  --disable-docs \
  --disable-werror \
  2>&1 |
  tee "$RUN_DIR/qemu-configure.log"

ninja \
  -j 8 \
  qemu-system-x86_64 \
  2>&1 |
  tee "$RUN_DIR/qemu-build.log"

QEMU=/home/users/u0001928/OCEAN_CXLMEMSIM/library/qemu/build-rccs-phase1/qemu-system-x86_64

test -x "$QEMU" &&
  echo "QEMU_BUILD=PASS" ||
  echo "QEMU_BUILD=FAIL"

"$QEMU" --version |
  head -3 |
  tee "$RUN_DIR/qemu-version.txt"

"$QEMU" -netdev help \
  2>&1 |
  tee "$RUN_DIR/qemu-netdevs.txt"
```

If Meson reports a GLib/compiler size mismatch, stop and transfer the complete
`qemu-configure.log` plus:

```text
library/qemu/build-rccs-phase1/meson-logs/meson-log.txt
```

Do not reuse the QEMU binary from `OCEAN_RCCS`; Phase 1 must identify the exact
legacy source under test.

## 11. Start the full server in TCP mode

```bash
SERVER=/home/users/u0001928/OCEAN_CXLMEMSIM/build-rccs-phase1-server/cxlmemsim_server

"$SERVER" \
  --comm-mode tcp \
  --capacity 1024 \
  --port 9999 \
  >"$RUN_DIR/server-runtime.log" \
  2>&1 &

SERVER_PID=$!
echo "$SERVER_PID" >"$RUN_DIR/server.pid"

sleep 2

if kill -0 "$SERVER_PID" 2>/dev/null; then
  echo "SERVER_RUNTIME=PASS"
else
  echo "SERVER_RUNTIME=FAIL"
  tail -100 "$RUN_DIR/server-runtime.log"
  exit 1
fi
```

Keep this process running while QEMU is active.

## 12. Launch one rootless TCP CXL VM

Use the complete image already stored in `OCEAN_RCCS`. The backing and LSA
files are separate from the TCP server and are private to this run.

```bash
QEMU=/home/users/u0001928/OCEAN_CXLMEMSIM/library/qemu/build-rccs-phase1/qemu-system-x86_64
KERNEL=/home/users/u0001928/OCEAN_RCCS/assets/author-20260720/bzImage
DISK=/home/users/u0001928/OCEAN_RCCS/assets/author-20260720/qemu.img

CXL_DIR=/dev/shm/ocean-cxl-phase1-${SLURM_JOB_ID:-manual}
CXL_MEM=$CXL_DIR/cxl-mem.raw
CXL_LSA=$CXL_DIR/cxl-lsa.raw

mkdir -p "$CXL_DIR"
truncate -s 1G "$CXL_MEM"
truncate -s 2M "$CXL_LSA"

export CXL_TRANSPORT_MODE=tcp
export CXL_MEMSIM_HOST=127.0.0.1
export CXL_MEMSIM_PORT=9999
export CXL_LATENCY_INJECT=0

set -o pipefail

"$QEMU" \
  --enable-kvm \
  -cpu qemu64,+xsave,+rdtscp,+avx,+avx2,+sse4.1,+sse4.2,+clflushopt \
  -smp 4 \
  -machine q35,cxl=on \
  -m 4G,slots=8,maxmem=16G \
  -object memory-backend-file,id=cxl-mem1,share=on,mem-path="$CXL_MEM",size=1G \
  -object memory-backend-file,id=cxl-lsa1,share=on,mem-path="$CXL_LSA",size=2M \
  -device pxb-cxl,bus_nr=52,bus=pcie.0,id=cxl.1 \
  -device cxl-rp,port=0,bus=cxl.1,id=cxl-rp0,chassis=0,slot=0 \
  -device cxl-type3,bus=cxl-rp0,persistent-memdev=cxl-mem1,lsa=cxl-lsa1,id=cxl-pmem0,sn=0x1 \
  -M cxl-fmw.0.targets.0=cxl.1,cxl-fmw.0.size=1G \
  -kernel "$KERNEL" \
  -append "root=/dev/vda rw console=ttyS0,115200 nokaslr cxl_region_size=1G" \
  -drive file="$DISK",if=none,id=osdisk,format=raw \
  -device virtio-blk-pci,drive=osdisk,bus=pcie.0 \
  -netdev user,id=net0 \
  -device virtio-net-pci,netdev=net0,mac=52:54:00:10:00:01,bus=pcie.0 \
  -fsdev local,security_model=none,id=fsdev0,path=/dev/shm \
  -device virtio-9p-pci,id=fs0,fsdev=fsdev0,mount_tag=hostshm,bus=pcie.0 \
  -snapshot \
  -nographic \
  2>&1 |
  tee "$RUN_DIR/qemu-runtime.log"
```

For `-nographic`, exit QEMU with:

```text
Ctrl-a x
```

After QEMU exits:

```bash
kill "$SERVER_PID" 2>/dev/null || true
wait "$SERVER_PID" 2>/dev/null || true
```

## 13. Guest checks

Log in as guest root. Do not convert the DAX device to system RAM.

First collect state:

```bash
echo "===== OS ====="
cat /etc/os-release
uname -a

echo "===== CXL ====="
cxl list
ndctl --version
ndctl list -R -N -D

echo "===== DAX ====="
daxctl list
ls -l /dev/dax*

echo "===== stale setup processes ====="
ps -ef |
  grep -E '[n]dctl|[u]dev'

echo "===== service ====="
systemctl status cxl-numa-setup.service --no-pager -l || true

echo "===== kernel ====="
dmesg |
  grep -iE 'cxl|dax' |
  tail -200
```

If `/dev/dax0.0` already exists, do not run `ndctl create-namespace`,
`ndctl destroy-namespace`, or `daxctl reconfigure-device`.

Run a small aligned devdax read/write test:

```bash
python3 - <<'PY'
import mmap
import os

path = "/dev/dax0.0"
length = 2 * 1024 * 1024
pattern = bytes(range(64))

fd = os.open(path, os.O_RDWR)
try:
    mapping = mmap.mmap(
        fd,
        length,
        flags=mmap.MAP_SHARED,
        prot=mmap.PROT_READ | mmap.PROT_WRITE,
    )
    try:
        mapping[0:64] = pattern
        observed = mapping[0:64]
        print("DEVDAX_RESULT=PASS" if observed == pattern else "DEVDAX_RESULT=FAIL")
        print("observed_hex=" + observed.hex())
    finally:
        mapping.close()
finally:
    os.close(fd)
PY
```

Start with this single-cacheline test. Do not run the legacy million-iteration
`test_cxl_mem` yet.

## 14. Return logs to the Mac

On the RCCS login node, after the compute step has ended:

```bash
cd /home/users/u0001928/ocean-phase1-logs

tar \
  -czf "$RUN_ID.tar.gz" \
  "$RUN_ID"

ls -lh "$RUN_ID.tar.gz"
```

If `RUN_ID` is no longer defined, identify it with:

```bash
ls -1dt /home/users/u0001928/ocean-phase1-logs/* |
  head
```

Run from the Mac:

```bash
mkdir -p /Users/guanghongxu/rccs-projects/ocean-phase1-logs

scp \
  u0001928@login.cloud.r-ccs.riken.jp:/home/users/u0001928/ocean-phase1-logs/<RUN_ID>.tar.gz \
  /Users/guanghongxu/rccs-projects/ocean-phase1-logs/

tar \
  -xzf /Users/guanghongxu/rccs-projects/ocean-phase1-logs/<RUN_ID>.tar.gz \
  -C /Users/guanghongxu/rccs-projects/ocean-phase1-logs/
```

The local agent should inspect at least:

- `git-head.txt`;
- `git-status.txt`;
- `server-configure.log`;
- `server-build.log`;
- `server-runtime.log`;
- `qemu-configure.log`;
- `qemu-build.log`;
- `qemu-runtime.log`;
- the complete QEMU Meson log when configuration fails.

## 15. Cluster-to-Mac emergency source recovery

The preferred rule is: never edit tracked source on RCCS. If an emergency
change is made there, return it before the next Mac-to-RCCS bundle:

On RCCS:

```bash
cd /home/users/u0001928/OCEAN_CXLMEMSIM

git diff --binary \
  > /home/users/u0001928/rccs-emergency-change.patch
```

On the Mac:

```bash
scp \
  u0001928@login.cloud.r-ccs.riken.jp:/home/users/u0001928/rccs-emergency-change.patch \
  /Users/guanghongxu/rccs-projects/

cd /Users/guanghongxu/rccs-projects/OCEAN_fam_emu_cxlmemsim

git apply --check \
  /Users/guanghongxu/rccs-projects/rccs-emergency-change.patch

git apply \
  /Users/guanghongxu/rccs-projects/rccs-emergency-change.patch
```

Review and commit the recovered change locally, then resume the normal bundle
workflow.

## 16. Rules that prevent source drift

Before every build, record:

```bash
git rev-parse HEAD
git status --short
```

The commit printed by the Mac and RCCS copies must match.

Never synchronize:

- `build-*`;
- `library/qemu/build-*`;
- `qemu.img`, `qemu1.img`, or `bzImage`;
- `/dev/shm` files;
- server PID files;
- runtime logs;
- core dumps.

Do not use `scp -r` on the whole repository. Use the Git bundle for source and
the compressed log archive for results.
