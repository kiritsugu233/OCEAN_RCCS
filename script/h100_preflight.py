#!/usr/bin/env python3
"""Small, dependency-free H100 preflight with auditable artifacts.

The probe intentionally does not load a model or run an OCEAN policy.  It
checks the immutable model revision, CUDA Driver API, Nsight Systems, and NUMA
first-touch placement before a measured H100 run is attempted.
"""

from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import hashlib
import json
import mmap
import os
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence


IMMUTABLE_REVISION = re.compile(r"^[0-9a-fA-F]{40,64}$")


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_command(argv: Sequence[str], timeout: int = 60) -> dict[str, Any]:
    executable = shutil.which(argv[0])
    if executable is None:
        return {
            "argv": list(argv),
            "available": False,
            "returncode": None,
            "stdout": "",
            "stderr": f"required executable {argv[0]!r} is not available",
        }
    try:
        completed = subprocess.run(
            list(argv),
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
        return {
            "argv": list(argv),
            "executable": executable,
            "available": True,
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "argv": list(argv),
            "executable": executable,
            "available": True,
            "returncode": None,
            "stdout": exc.stdout or "",
            "stderr": (exc.stderr or "") + f"\ncommand timed out after {timeout}s",
        }


def git_metadata(repo_root: Path) -> dict[str, Any]:
    revision = run_command(["git", "-C", str(repo_root), "rev-parse", "HEAD"])
    status = run_command(
        ["git", "-C", str(repo_root), "status", "--porcelain", "--untracked-files=normal"]
    )
    return {
        "revision": revision["stdout"].strip() if revision["returncode"] == 0 else None,
        "dirty": bool(status["stdout"].strip()) if status["returncode"] == 0 else None,
        "revision_command": revision,
        "status_command": status,
    }


def _huggingface_cache_roots() -> list[Path]:
    roots: list[Path] = []
    if os.environ.get("HF_HUB_CACHE"):
        roots.append(Path(os.environ["HF_HUB_CACHE"]))
    if os.environ.get("HUGGINGFACE_HUB_CACHE"):
        roots.append(Path(os.environ["HUGGINGFACE_HUB_CACHE"]))
    if os.environ.get("HF_HOME"):
        roots.append(Path(os.environ["HF_HOME"]) / "hub")
    roots.append(Path.home() / ".cache" / "huggingface" / "hub")
    unique: list[Path] = []
    for root in roots:
        if root not in unique:
            unique.append(root)
    return unique


def resolve_model_revision(model: str, requested: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "model": model,
        "requested_revision": requested,
        "resolved_revision": None,
        "immutable": False,
        "source": None,
    }
    if IMMUTABLE_REVISION.fullmatch(requested):
        result.update(
            resolved_revision=requested.lower(), immutable=True, source="requested_revision"
        )
        return result

    model_path = Path(model).expanduser()
    if model_path.exists():
        for parent in [model_path, *model_path.parents]:
            if parent.parent.name == "snapshots" and IMMUTABLE_REVISION.fullmatch(parent.name):
                result.update(
                    resolved_revision=parent.name.lower(),
                    immutable=True,
                    source="snapshot_path",
                )
                return result
        git = run_command(
            ["git", "-C", str(model_path), "rev-parse", f"{requested}^{{commit}}"]
        )
        resolved = git["stdout"].strip()
        if git["returncode"] == 0 and IMMUTABLE_REVISION.fullmatch(resolved):
            result.update(
                resolved_revision=resolved.lower(), immutable=True, source="model_git"
            )
            return result

    cache_name = "models--" + model.replace("/", "--")
    for cache_root in _huggingface_cache_roots():
        ref = cache_root / cache_name / "refs" / requested
        if not ref.is_file():
            continue
        resolved = ref.read_text(encoding="utf-8").strip()
        if IMMUTABLE_REVISION.fullmatch(resolved):
            result.update(
                resolved_revision=resolved.lower(),
                immutable=True,
                source=str(ref),
            )
            return result
    result["error"] = (
        "model revision did not resolve to an immutable 40-64 character hex commit"
    )
    return result


def parse_cpu_list(value: str) -> list[int]:
    cpus: list[int] = []
    for part in value.strip().split(","):
        if not part:
            continue
        bounds = part.split("-", 1)
        first = int(bounds[0])
        last = int(bounds[1]) if len(bounds) == 2 else first
        cpus.extend(range(first, last + 1))
    return cpus


def numa_topology(sysfs_root: Path = Path("/sys/devices/system/node")) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    for path in sorted(sysfs_root.glob("node[0-9]*"), key=lambda item: int(item.name[4:])):
        node_id = int(path.name[4:])
        cpulist = (path / "cpulist").read_text(encoding="utf-8").strip()
        distance_path = path / "distance"
        meminfo_path = path / "meminfo"
        nodes.append(
            {
                "node": node_id,
                "cpulist": cpulist,
                "cpus": parse_cpu_list(cpulist),
                "distance": distance_path.read_text(encoding="utf-8").strip()
                if distance_path.exists()
                else None,
                "meminfo": [
                    line.strip()
                    for line in meminfo_path.read_text(encoding="utf-8").splitlines()
                    if "MemTotal" in line or "MemFree" in line
                ]
                if meminfo_path.exists()
                else [],
            }
        )
    return nodes


def _numa_probe_child(node: int, size_mib: int = 16) -> int:
    topology = numa_topology()
    selected = next((item for item in topology if item["node"] == node), None)
    if selected is None or not selected["cpus"]:
        print(json.dumps({"node": node, "passed": False, "error": "node has no CPUs"}))
        return 2
    allowed = set(os.sched_getaffinity(0))
    cpus = sorted(allowed.intersection(selected["cpus"]))
    if not cpus:
        print(
            json.dumps(
                {
                    "node": node,
                    "passed": False,
                    "error": "Slurm CPU affinity does not include a CPU from this node",
                    "allowed_cpus": sorted(allowed),
                }
            )
        )
        return 2
    os.sched_setaffinity(0, {cpus[0]})
    size = size_mib * 1024 * 1024
    allocation = mmap.mmap(-1, size, flags=mmap.MAP_PRIVATE | mmap.MAP_ANONYMOUS)
    for offset in range(0, size, mmap.PAGESIZE):
        allocation[offset] = 1
    address = ctypes.addressof(ctypes.c_char.from_buffer(allocation))
    prefix = f"{address:x}"
    numa_line = next(
        (
            line.strip()
            for line in Path("/proc/self/numa_maps").read_text(encoding="utf-8").splitlines()
            if line.startswith(prefix + " ")
        ),
        "",
    )
    pages_by_node = {
        int(match.group(1)): int(match.group(2))
        for match in re.finditer(r"\bN(\d+)=(\d+)\b", numa_line)
    }
    placed_pages = pages_by_node.get(node, 0)
    total_pages = sum(pages_by_node.values())
    result = {
        "node": node,
        "cpu": cpus[0],
        "bytes": size,
        "pages_by_node": pages_by_node,
        "target_ratio": placed_pages / total_pages if total_pages else 0.0,
        "numa_maps": numa_line,
        "passed": total_pages > 0 and placed_pages == total_pages,
    }
    print(json.dumps(result, sort_keys=True))
    allocation.close()
    return 0 if result["passed"] else 2


def collect_numa_placement(script: Path) -> dict[str, Any]:
    topology = numa_topology()
    if not hasattr(os, "sched_getaffinity"):
        return {
            "topology": topology,
            "process_affinity": [],
            "probes": [],
            "passed": False,
            "error": "NUMA affinity probing requires Linux",
        }
    probes = []
    for node in topology:
        command = run_command(
            [sys.executable, str(script), "--numa-probe-child", str(node["node"])],
            timeout=30,
        )
        parsed: dict[str, Any]
        try:
            parsed = json.loads(command["stdout"].strip().splitlines()[-1])
        except (IndexError, json.JSONDecodeError):
            parsed = {"passed": False, "error": "NUMA child produced no valid JSON"}
        parsed["command"] = command
        probes.append(parsed)
    return {
        "topology": topology,
        "process_affinity": sorted(os.sched_getaffinity(0)),
        "probes": probes,
        "passed": bool(probes) and all(probe.get("passed") for probe in probes),
    }


def failed_check(name: str, exc: Exception) -> dict[str, Any]:
    return {"passed": False, "error": f"{type(exc).__name__}: {exc}", "check": name}


def cuda_driver_probe() -> dict[str, Any]:
    result: dict[str, Any] = {"passed": False}
    try:
        library = ctypes.CDLL("libcuda.so.1")
        library.cuInit.argtypes = [ctypes.c_uint]
        library.cuInit.restype = ctypes.c_int
        library.cuDeviceGetCount.argtypes = [ctypes.POINTER(ctypes.c_int)]
        library.cuDeviceGetCount.restype = ctypes.c_int
        library.cuDeviceGet.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.c_int]
        library.cuDeviceGet.restype = ctypes.c_int
        library.cuDevicePrimaryCtxRetain.argtypes = [
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.c_int,
        ]
        library.cuDevicePrimaryCtxRetain.restype = ctypes.c_int
        library.cuCtxSetCurrent.argtypes = [ctypes.c_void_p]
        library.cuCtxSetCurrent.restype = ctypes.c_int
        library.cuMemAlloc_v2.argtypes = [
            ctypes.POINTER(ctypes.c_ulonglong),
            ctypes.c_size_t,
        ]
        library.cuMemAlloc_v2.restype = ctypes.c_int
        library.cuMemsetD8_v2.argtypes = [
            ctypes.c_ulonglong,
            ctypes.c_ubyte,
            ctypes.c_size_t,
        ]
        library.cuMemsetD8_v2.restype = ctypes.c_int
        library.cuCtxSynchronize.argtypes = []
        library.cuCtxSynchronize.restype = ctypes.c_int
        library.cuMemFree_v2.argtypes = [ctypes.c_ulonglong]
        library.cuMemFree_v2.restype = ctypes.c_int
        init_status = library.cuInit(0)
        count = ctypes.c_int()
        count_status = library.cuDeviceGetCount(ctypes.byref(count)) if init_status == 0 else None
        operation_statuses: dict[str, int] = {}
        allocation = ctypes.c_ulonglong()
        if count_status == 0 and count.value == 1:
            device = ctypes.c_int()
            context = ctypes.c_void_p()
            operation_statuses["cuDeviceGet"] = library.cuDeviceGet(ctypes.byref(device), 0)
            if operation_statuses["cuDeviceGet"] == 0:
                operation_statuses["cuDevicePrimaryCtxRetain"] = library.cuDevicePrimaryCtxRetain(
                    ctypes.byref(context), device
                )
            if operation_statuses.get("cuDevicePrimaryCtxRetain") == 0:
                operation_statuses["cuCtxSetCurrent"] = library.cuCtxSetCurrent(context)
            if operation_statuses.get("cuCtxSetCurrent") == 0:
                operation_statuses["cuMemAlloc"] = library.cuMemAlloc_v2(
                    ctypes.byref(allocation), 4096
                )
            if operation_statuses.get("cuMemAlloc") == 0:
                operation_statuses["cuMemsetD8"] = library.cuMemsetD8_v2(
                    allocation, 0xA5, 4096
                )
                operation_statuses["cuCtxSynchronize"] = library.cuCtxSynchronize()
                operation_statuses["cuMemFree"] = library.cuMemFree_v2(allocation)
        result.update(
            cuInit=init_status,
            cuDeviceGetCount=count_status,
            device_count=count.value if count_status == 0 else 0,
            device_operation_statuses=operation_statuses,
        )
        result["passed"] = (
            init_status == 0
            and count_status == 0
            and count.value == 1
            and len(operation_statuses) == 7
            and all(status == 0 for status in operation_statuses.values())
        )
    except OSError as exc:
        result["error"] = str(exc)
    return result


def collect_cuda() -> dict[str, Any]:
    query = run_command(
        [
            "nvidia-smi",
            "--query-gpu=index,name,uuid,memory.total,pci.bus_id,driver_version",
            "--format=csv,noheader",
        ]
    )
    topo = run_command(["nvidia-smi", "topo", "-m"])
    rows = [line.strip() for line in query["stdout"].splitlines() if line.strip()]
    driver = cuda_driver_probe()
    return {
        "visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES"),
        "nvidia_smi_query": query,
        "nvidia_smi_topology": topo,
        "driver_api": driver,
        "passed": query["returncode"] == 0
        and len(rows) == 1
        and "H100" in rows[0]
        and driver["passed"],
    }


def collect_nsight(output_dir: Path, script: Path) -> dict[str, Any]:
    version = run_command(["nsys", "--version"])
    environment = run_command(["nsys", "status", "--environment"])
    report_prefix = output_dir / "nsight-cuda-probe"
    profile = run_command(
        [
            "nsys",
            "profile",
            "--trace=cuda,osrt",
            "--force-overwrite=true",
            "--output",
            str(report_prefix),
            sys.executable,
            str(script),
            "--cuda-probe-child",
        ],
        timeout=120,
    )
    reports = [
        str(path.relative_to(output_dir))
        for suffix in (".nsys-rep", ".qdrep")
        for path in output_dir.glob(report_prefix.name + suffix)
    ]
    return {
        "version": version,
        "environment": environment,
        "profile_probe": profile,
        "reports": reports,
        "passed": version["returncode"] == 0
        and environment["returncode"] == 0
        and profile["returncode"] == 0
        and bool(reports),
    }


def _cuda_probe_child() -> int:
    result = cuda_driver_probe()
    print(json.dumps(result, sort_keys=True))
    return 0 if result["passed"] else 2


def command_summary(name: str, value: dict[str, Any], log: Any) -> None:
    print(f"\n===== {name} =====", file=log)
    for key in ("argv", "executable", "available", "returncode"):
        if key in value:
            print(f"{key}={value[key]}", file=log)
    if value.get("stdout"):
        print("--- stdout ---", file=log)
        print(value["stdout"].rstrip(), file=log)
    if value.get("stderr"):
        print("--- stderr ---", file=log)
        print(value["stderr"].rstrip(), file=log)


def write_logs(manifest: dict[str, Any], preflight_log: Path, failure_log: Path) -> None:
    with preflight_log.open("w", encoding="utf-8") as log:
        print(json.dumps(manifest, indent=2, sort_keys=True), file=log)
        repo = manifest.get("repository", {})
        command_summary("git revision", repo.get("revision_command", {}), log)
        command_summary("git status", repo.get("status_command", {}), log)
        cuda = manifest.get("cuda", {})
        command_summary("nvidia-smi query", cuda.get("nvidia_smi_query", {}), log)
        command_summary("nvidia-smi topology", cuda.get("nvidia_smi_topology", {}), log)
        nsight = manifest.get("nsight", {})
        command_summary("nsys version", nsight.get("version", {}), log)
        command_summary("nsys environment", nsight.get("environment", {}), log)
        command_summary("nsys CUDA profile", nsight.get("profile_probe", {}), log)

    failures = manifest.get("failures", [])
    details = {
        "model_revision": manifest.get("model", {}),
        "cuda": manifest.get("cuda", {}),
        "nsight": manifest.get("nsight", {}),
        "numa_placement": manifest.get("numa_placement", {}),
    }
    with failure_log.open("w", encoding="utf-8") as log:
        for item in failures:
            print(f"[{item['check']}] {item['message']}", file=log)
            print(json.dumps(details.get(item["check"], {}), indent=2, sort_keys=True), file=log)


def run_preflight(args: argparse.Namespace) -> int:
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "manifest.json"
    preflight_log = output_dir / "preflight.log"
    failure_log = output_dir / "failure.log"
    started = utc_now()
    failures: list[dict[str, str]] = []

    try:
        model = resolve_model_revision(args.model, args.model_revision)
    except Exception as exc:  # Preserve artifacts even when a probe is malformed.
        model = failed_check("model_revision", exc)
        model.update(
            model=args.model,
            requested_revision=args.model_revision,
            resolved_revision=None,
            immutable=False,
        )
    if not model["immutable"]:
        failures.append(
            {
                "check": "model_revision",
                "message": model.get("error", "model revision check failed"),
            }
        )

    try:
        cuda = collect_cuda()
    except Exception as exc:
        cuda = failed_check("cuda", exc)
    if not cuda["passed"]:
        failures.append(
            {"check": "cuda", "message": "H100 CUDA Driver API probe did not pass"}
        )

    try:
        numa = collect_numa_placement(Path(__file__).resolve())
    except Exception as exc:
        numa = failed_check("numa_placement", exc)
    if not numa["passed"]:
        failures.append(
            {
                "check": "numa_placement",
                "message": "one or more NUMA first-touch placement probes did not pass",
            }
        )

    try:
        nsight = collect_nsight(output_dir, Path(__file__).resolve())
    except Exception as exc:
        nsight = failed_check("nsight", exc)
        nsight["reports"] = []
    if not nsight["passed"]:
        failures.append(
            {"check": "nsight", "message": "Nsight CUDA profile probe did not pass"}
        )

    manifest: dict[str, Any] = {
        "schema_version": 1,
        "artifact_type": "h100_preflight",
        "provenance": "measured",
        "status": "failed" if failures else "passed",
        "started_at": started,
        "finished_at": utc_now(),
        "host": socket.gethostname(),
        "slurm": {
            key: os.environ.get(key)
            for key in (
                "SLURM_JOB_ID",
                "SLURM_JOB_NAME",
                "SLURM_JOB_PARTITION",
                "SLURM_JOB_NODELIST",
                "SLURM_CPUS_ON_NODE",
            )
        },
        "repository": git_metadata(args.repo_root.resolve()),
        "model": model,
        "cuda": cuda,
        "nsight": nsight,
        "numa_placement": numa,
        "checks": {
            "model_revision": model["immutable"],
            "cuda": cuda["passed"],
            "nsight": nsight["passed"],
            "numa_placement": numa["passed"],
        },
        "failures": failures,
        "artifacts": {
            "manifest": manifest_path.name,
            "preflight_log": preflight_log.name,
            "failure_log": failure_log.name,
            "nsight_reports": nsight["reports"],
        },
    }
    write_logs(manifest, preflight_log, failure_log)
    manifest["artifacts"]["preflight_log_sha256"] = sha256_file(preflight_log)
    manifest["artifacts"]["failure_log_sha256"] = sha256_file(failure_log)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"H100 preflight {manifest['status']}: {manifest_path}")
    return 0 if not failures else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--model", default="")
    parser.add_argument("--model-revision", default="")
    parser.add_argument("--output-dir", type=Path, default=Path("state/h100-preflight/local"))
    parser.add_argument("--numa-probe-child", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--cuda-probe-child", action="store_true", help=argparse.SUPPRESS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.numa_probe_child is not None:
        return _numa_probe_child(args.numa_probe_child)
    if args.cuda_probe_child:
        return _cuda_probe_child()
    return run_preflight(args)


if __name__ == "__main__":
    raise SystemExit(main())
