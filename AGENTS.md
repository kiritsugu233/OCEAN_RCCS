# Repository Guidelines

## Project Structure & Modules
- Source: `src/` (core C++: controller, server, helpers), headers in `include/`.
- Microbenchmarks: `microbench/` (C/C++ workloads and utilities).
- Integration: `qemu_integration/`, optional libs in `lib/` (e.g., qemu, bpftime).
- Scripts & demos: top-level `run_demo.sh`, `run_protocol_demo.sh`.
- Build artifacts: `build/` (CMake), logs in repo root, shared memory at `/dev/shm/cxlmemsim_shared`.

## Build, Test, and Development
- Configure and build:
  - `cmake -S . -B build -DCMAKE_BUILD_TYPE=Release`
  - `cmake --build build -j` (targets include `cxlmemsim_server`, microbench apps)
- Dependencies: CMake ≥ 3.11, GCC/Clang, `libspdlog-dev`, `libcxxopts-dev`, Linux headers (for BPF pieces).
- Run server:
  - `./build/cxlmemsim_server --port 9999 --capacity 256`
  - Optional env: `SPDLOG_LEVEL=debug`, `CXL_BASE_ADDR=0`
- Quick demos/tests:
  - Shared-memory demo: `./run_demo.sh`
  - Protocol demo: `./run_protocol_demo.sh`
  - Direct test binaries: `./test_cxl_numa`, `./test_protocol_pattern`, etc.

## Coding Style & Naming
- Formatting: `.clang-format` (LLVM base, 4 spaces, 120 col). Run `clang-format -i` before committing.
- Linting: `.clang-tidy` enabled; prefer modern C++ idioms (`std::unique_ptr`, `noexcept`, `nullptr`).
- Naming: types `CamelCase`, functions `lowerCamelCase`, variables `snake_case`, constants `kCamelCase`.
- Headers in `include/`; keep public APIs minimal and documented in headers.

## Testing Guidelines
- Prefer small, self-contained executables in `src/` or `microbench/` for behavior checks.
- Add new demos or tests mirroring existing `test_*.c/cc` patterns; name clearly by feature (e.g., `test_numa_pattern.c`).
- Validate via scripts: `run_demo.sh`, `run_protocol_demo.sh`, `show_test_results.sh`.

## Commit & Pull Requests
- Commits: concise, component-scoped subject. Example: `server: add back-invalidation tracking`.
- Include rationale and any perf/latency impact in the body.
- PRs: describe change, link issues, include run commands and expected output, attach logs (e.g., `server_demo.log`) or screenshots.
- CI-style checks: build cleanly with Release and Debug, run both demos, pass `clang-format`/`clang-tidy`.

## Security & Configuration Tips
- Server mediates access; clients should use protocol paths (see `protocol_{reader,writer}.c`).
- Shared memory size and base are configured by server; avoid hardcoding `/dev/shm` offsets.
- Log levels via `SPDLOG_LEVEL` (e.g., `info`, `debug`) for reproducible reports.

## LLM Offloading Extension Rules

These rules supplement the original OCEAN repository guidelines. They do not
replace upstream behavior, APIs, tests, or coding conventions.

1. OCEAN remains a general-purpose CXL-memory simulator. LLM weight/KV
   offloading support must be additive and must not silently change existing
   non-LLM behavior.

2. The LLM integration consumes logical transfer/replay inputs with stable
   request, object, access, placement, transfer, and dependency identity.
   Preserve these identities through ingestion, scheduling, service, and output.

3. OCEAN owns modeled memory-service, queueing, contention, and topology time.
   Execution-DAG replay may propagate dependencies but must not add the same
   service time a second time.

4. Measured completion times, transfer waits, or final LLM performance results
   must never be replayed as simulator answers. They are calibration or
   evaluation targets outside the modeled execution path.

5. Preserve provenance:

   - local NUMA measurement: `measured_local_numa`
   - remote NUMA observation: `measured_remote_numa_proxy`
   - CXLMemSim/CXL-link result: `modeled_cxl_link`
   - physical CXL measurement: only when real CXL hardware produced it

   Modeled, NUMA-proxy, QEMU, or TCP results must never be described as physical
   CXL measurements.

6. Preserve existing OCEAN public APIs, protocols, demos, and upstream tests.
   Any incompatible change requires explicit user approval and a versioned
   compatibility migration.

7. Do not modify Phase 4 case identities, calibration/validation splits,
   held-out sealing, scientific thresholds, or Slurm launch policy from this
   repository. Those contracts are owned by the main `cxl-llm-step1` repository.

8. Keep the two repositories independent:

   - use OCEAN branch `agent/ocean-core-kv-weight-replay`;
   - verify `git remote -v`, branch, upstream, HEAD, and working tree before work;
   - never mix main-repository and OCEAN commits;
   - never force-push, use `reset --hard`, or overwrite user changes.

9. LLM-related changes must be surgical. Do not perform opportunistic refactors,
   broad formatting, API cleanup, dependency upgrades, or unrelated performance
   changes. Format only files or ranges required by the approved change.

10. Before editing, list the expected files and justify each one. Pause for user
    approval before commit if the change exceeds 8 tracked files or 500 total
    added/deleted lines, or if it touches code outside the stated boundary.

11. Every OCEAN LLM change must include focused regression tests proving:

    - identity preservation;
    - no service-time double counting;
    - deterministic replay;
    - correct failure retention;
    - unchanged non-LLM behavior.

12. Passing tests does not justify expanded scope. Prefer the smallest
    implementation that enforces the approved invariant.
