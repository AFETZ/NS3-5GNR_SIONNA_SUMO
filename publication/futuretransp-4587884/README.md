# Final manuscript analysis: 140 EVA + 27 intersection runs

Materials for “Impact of 5G NR-V2X Message Losses on Connected Vehicle Warning
Functions” (futuretransp-4587884). Three files provide the inputs and numerical
analysis used in the final manuscript:

- `data.json.gz`: one gzip-compressed JSON document with the retained EVA tables,
  the 27 included intersection MSG/CTRL/XML records, counts and sample metadata.
- `analyze.py`: recomputes the reported statistics and application-event matches.
- This README: commands, definitions and scope.

## Run

Use Python 3.11 or newer, without `-O`:

```sh
python -m pip install numpy==2.3.5
python analyze.py --output results.json
```

No simulator or network is required after installing NumPy. The script checks
the data checksum, then writes one results document: summary, 14 EVA power
summaries, 27 intersection run summaries, three mode summaries and 281
CAM-to-controller matches. Figures and duplicate result files are not bundled.
To inspect the inputs, decompress `data.json.gz` as standard gzip/JSON.

## Inputs and definitions

The JSON keys `eva_runs` and `eva_receivers` contain 140 run records and 2660
receiver histories. Power is dBm and times are simulation seconds. Historical
`reaction_delay` fields mean first CAM reception minus the sender's first
transmission, not a controller reaction. PRR is successful station-2 receptions
divided by 19 times its CAM transmission count. Receiver eligibility/range is
not filtered. Three histories at −20 dBm are censored at 60 s. The primary
within-run P90 uses successful receptions; window substitution is a separate
sensitivity. The curve is the median of ten run P90s per power.

`intersection_counts` contains retained sender counts and receiver/control
comparison values. `intersection_records` stores the original UTF-8 contents
of MSG, CTRL and collision XML for the same nine RngRun IDs (1–8, 11) per mode.
Receiver counts, accepted control events, collisions and CAM matches are read
from those records. Sender counts are retained table values; not all sender
logs are available. Missing/malformed collision XML is an error, never zero.
`intersection_metadata` and `run_metadata` supply durations, source revision
and runtimes; intersection runtime is counted once per three-mode seed batch.

`selection_ledger` records availability/inclusion for the 90 original cases,
without their historical outcomes: 59 lacked required files, four complete
cases lacked complete cross-mode counterparts, and 27 were included. Its
consistency is checked; excluded raw files are not included or re-audited.
Selection conditions on recorded accepted-controller activity.

Bootstrap intervals use NumPy `default_rng(204)`, reset per group/statistic,
10,000 resamples for EVA and 20,000 for intersection, with linear quantiles.
Intersection AoI is control time minus the newest generation time already
received; `cam_gdt_ms` is converted from milliseconds. Radar-only AoI is
undefined. Invalid packet UIDs are not join keys.

| Intersection mode | Runs | Mean PRR | Median first control (s) | Collision runs | Median AoI (ms) |
| --- | ---: | ---: | ---: | ---: | ---: |
| Degraded V2X | 9 | 0.682 | 4.795 | 9/9 | 26.83 |
| Radar only | 9 | n/a | 5.000 | 9/9 | undefined |
| High-quality V2X | 9 | 0.925 | 2.068 | 0/9 | 4.83 |

## Recorded configuration and scope

EVA uses 60 s runs, RngRun 1–10 at 14 transmit powers, with sensing, channel
randomness and Sionna disabled. Intersection runs last 20 s, use Sionna and
local radar-like detection, and apply a receiver impairment treatment rather
than isolate contention/SPS. Source reconstruction gives a 40 MHz sidelink
PHY, numerology 2, 53 RB and five complete 10-RB subchannels; this is not an
independent measurement of the historical executable.
