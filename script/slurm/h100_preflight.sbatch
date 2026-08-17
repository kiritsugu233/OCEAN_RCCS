#!/usr/bin/env bash
#SBATCH --job-name=ocean-h100-preflight
#SBATCH --partition=ai-h100l
#SBATCH --nodelist=ai-h100l-00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --time=00:10:00
#SBATCH --output=slurm-%x-%j.out

# RCCS ai-h100l exposes the whole dedicated H100 node but does not register a
# GPU GRES and advertises only 1 MiB RealMemory.  Do not add --gres or --mem.

set -u

OCEAN_REPO="${OCEAN_REPO:-${SLURM_SUBMIT_DIR}}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
OUTPUT_DIR="${OCEAN_PREFLIGHT_OUTPUT_DIR:-${OCEAN_REPO}/state/h100-preflight/${SLURM_JOB_ID}}"

cd "${OCEAN_REPO}" || exit 2
exec "${PYTHON_BIN}" "${OCEAN_REPO}/script/h100_preflight.py" \
  --repo-root "${OCEAN_REPO}" \
  --model "${H100_MODEL_ID:-}" \
  --model-revision "${H100_MODEL_REVISION:-}" \
  --output-dir "${OUTPUT_DIR}"
