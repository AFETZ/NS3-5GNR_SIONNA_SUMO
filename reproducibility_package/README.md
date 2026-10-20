# Manuscript reproducibility package

This package reproduces the manuscript analyses of 140 emergency-vehicle alert (EVA) runs and 27 complete-case intersection runs. The analysis uses the retained records and requires no simulator or network connection after the package and Python dependency have been downloaded.

## Reproduce the analysis

Requirements: Python 3.11 or later and NumPy 2.3.5. From this directory, run:

```bash
python -m pip install numpy==2.3.5
python analyze.py --output results.json
```

Run Python without `-O`, because the validation assertions must remain enabled. The script verifies the SHA-256 checksums of the four data archives before analysis. A successful run reports 140 EVA runs, 27 intersection runs, 281 CAM-control links, and 81/81 EVA raw-log checks passed.

## Package contents

- `analyze.py`: analysis and validation script.
- `data.json.gz`: EVA run and receiver tables, intersection counts and metadata, the selection ledger, and the included intersection MSG, CTRL, and collision XML records.
- `eva_logs.zip`: 81 complete EVA MSG sets for independent checks against the retained run table.
- `launch_configurations.zip`: recorded launches, selected profile and simulator logs, and source snapshots with revision metadata.
- `phy_sources.zip`: historical PHY source excerpts, parameter records, and source checksums.

## Incomplete runs by information mode

The following counts come from the retained intersection selection ledger.

| Information mode | MSG missing or empty | CTRL missing or empty | Collision XML missing or empty | Incomplete runs |
|---|---:|---:|---:|---:|
| Radar + degraded V2X | 19 | 19 | 19 | 19 |
| Radar only | 21 | 21 | 20 | 21 |
| Radar + high-quality V2X | 19 | 19 | 19 | 19 |
| **Total** | **59** | **59** | **58** | **59** |

A run may be counted in more than one missing-or-empty-file column. These columns must not be added together to determine the number of incomplete runs.

Of the 90 intersection ledger entries, 59 have incomplete required records. The primary analysis includes the nine common `RngRun` values (1-8 and 11) in all three modes, giving 27 runs. Four additional entries have complete required records but lack a complete counterpart in every mode and are therefore outside the matched analysis.

## Evidence scope and source revision

The EVA analysis reaggregates the retained tables for all 140 runs and independently checks the 81 runs with complete packaged MSG sets. The intersection analysis recomputes metrics from the included receiver MSG, CTRL, and collision XML records for the 27 selected runs. Excluded raw intersection files are not supplied; the selection checks establish consistency of the retained ledger.

The source snapshots in the ZIP archives record the historical base revision `aeda8b4d6f9a50bb02aa83cfdf893a3584e21460`. The executed local intersection launcher, controller, and configuration changes were not captured in that revision. The package supports reanalysis of the retained evidence; it does not establish exact rerun reproducibility of the historical simulations.

For citation, use the full Git commit SHA of the package version, with a URL of the form `https://github.com/AFETZ/NS3-5GNR_SIONNA_SUMO/tree/<commit-sha>/reproducibility_package`. This identifies a fixed package version even if `main` later changes.
