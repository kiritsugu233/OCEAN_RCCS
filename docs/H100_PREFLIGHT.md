# H100 preflight

This is a small, fail-fast gate for measured runs on RCCS `ai-h100l-00`. It
does not load the checkpoint and does not select or add an OCEAN policy.

Submit it with an immutable Hugging Face revision (or a symbolic revision that
already resolves in the local Hugging Face cache):

```bash
export OCEAN_REPO=/path/to/OCEAN_fam_emu_cxlmemsim
export H100_MODEL_ID=Qwen/Qwen2.5-1.5B-Instruct
export H100_MODEL_REVISION=<40-character-checkpoint-commit>
sbatch --export=ALL "$OCEAN_REPO/script/slurm/h100_preflight.sbatch"
```

The job deliberately requests neither `--mem` nor `--gres`, matching the RCCS
`ai-h100l` configuration. It writes the following files under
`state/h100-preflight/<job-id>/`:

- `manifest.json`: repository and model revisions, Slurm metadata, CUDA Driver
  API and `nvidia-smi` results, Nsight environment/profile status, and NUMA
  topology plus first-touch placement evidence;
- `preflight.log`: full command output for diagnosis;
- `failure.log`: one concise line per failed gate (empty on success);
- `nsight-cuda-probe.nsys-rep` (or `.qdrep`): the tiny CUDA Driver API trace.

Do not start a measured model run unless `manifest.json` has `status: passed`.
