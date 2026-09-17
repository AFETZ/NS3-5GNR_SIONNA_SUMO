# Analysis amendments after the execution freeze

The execution inputs, treatment levels, scenario files, simulator source, and
planned 410-cell matrix were frozen before production. The research container
records their byte-level hashes in `/research/environment-manifest.json`; each
run also records exact commands and input/output hashes. These analysis changes
were made while the matrix was running, before aggregate production outcomes
were inspected. They do not change any simulator input or treatment.

1. The intersection analyzer now refuses a partial or mixed-arm cohort and
   verifies every block, run manifest, artifact hash, 40-MHz/53-RB radio audit,
   Sionna no-ray audit, and matched pre-intervention SUMO trajectory. This
   strengthens the eligibility gate for the prospectively specified paired
   comparison.
2. An application reception at exactly 5.000 s is excluded from the declared
   half-open `[2.0, 5.0)`-s calibration window. A boundary test fixes this
   interpretation. No numerator or denominator was inspected to choose the
   rule.
3. The behavioral summary reports Kaplan–Meier first-command medians/P90 with
   no-action runs right-censored at the completed 20-s horizon. It retains
   source-specific command counts and does not label a sensor observation as a
   command.
4. The figure generator leaves non-estimable emergency P90 cells as gaps and
   uses literal simulator-pipeline and receiver-noise labels. A scene plan view
   is generated from the frozen SUMO lanes, routes, and PLY mesh bounds.
5. A publication-packaging command checks the exact completed matrix and every
   artifact digest before writing a deterministic archive. It does not include
   NVIDIA/OptiX runtime binaries.

The final publication archive contains the exact analysis scripts used to
produce its summaries and figures, with a SHA-256 index. Their final byte
hashes can differ from the preproduction environment manifest. The manuscript
must cite the final analysis and execution provenance separately.

## Propagation-model audit and separate urban replication

Before aggregate production outcomes were inspected, a source audit identified
that the original example selects `BandwidthPartInfo::V2V_Highway` for every
run. The NR helper maps this choice to the 3GPP TR 37.885 highway path-loss
and channel-condition models. The native intersection arms also lack ns-3
`Building` objects, although the co-registered Sionna scene contains two
synthetic building blocks. This is a substantive model mismatch for an
urban-like intersection; the frozen 410-cell execution is preserved unchanged.
Its intersection native-versus-Sionna contrast must be labelled a comparison
of the specified highway-native and Sionna pipelines, not a controlled urban
propagation comparison. The emergency-road power sweep is unaffected by the
intersection-specific geometry amendment.

An independently versioned **post-freeze urban replication** will repeat all
five behavioral and four radio-calibration intersection arms at the same 30
block identifiers (270 new cells). The new example will explicitly select
`V2V_Urban` and create two ns-3 Buildings with the XY footprints and heights
recorded in `scene.manifest.json`; the existing Sionna meshes remain the
counterpart radio geometry. It will log the model choice and building bounds,
and the runner will reject a run lacking the expected audit markers. A
preproduction pilot will check representative line-of-sight and blocked links
and the complete input/output trace contract; pilot outcomes will not enter
estimation. The urban cohort uses a separate build, environment manifest, run
tree, plan, and publication archive, with unchanged seeds, traffic routes,
receiver-noise levels, controller settings, eligibility windows, and analysis
definitions. Comparability of pre-intervention trajectories and input hashes
will be checked explicitly. A different source and channel model means the
original and urban native arms are distinct treatments; results will never be
silently pooled. If all gates pass, the urban cohort supplies the main
intersection comparison and the original highway-native results form a model
sensitivity analysis. This amendment is not presented as prospectively
specified in the original 410-cell protocol.

## Sionna vehicle-height correction before the urban estimate

A second source audit found that TraCI already gives the ns-3 mobility node
`Altitude=1.5` m and sends that z coordinate to Sionna. The legacy Sionna
server added a further 1.5 m antenna displacement and placed the center of a
1.3-m-high car mesh at the incoming z. Thus legacy ray-tracing antennas were
at 3.0 m and the car meshes floated above the ground. The original frozen
cohort cannot be changed; its Sionna arms must be identified by this geometry
and cannot be used as a matched-antenna-height comparator.

The first urban matrix was stopped before any aggregate outcome inspection:
its last durable progress record listed 165 completed cells, 104 pending, and
one interrupted cell. Every cell in this attempt is excluded from estimation,
including the 165 that completed. Its dedicated Docker volume and manifests
are retained as an audit trail. The corrected urban replication is a fresh
270-cell matrix in a new volume with a new frozen source/environment manifest.
It keeps the ns-3 antenna at 1.5 m and, in an explicit Sionna server geometry
mode, places the ray-tracing antenna at the incoming 1.5-m z coordinate while
placing the car mesh center at 0.65 m, so the 1.3-m mesh spans road level to
1.3 m. The runner must log and validate this geometry in each Sionna run, and
new excluded pilots must verify no-ray diagnostics and paired trajectories
before production. No arm, treatment level, traffic route, seed, horizon,
eligibility window, or outcome definition is chosen using the interrupted
cohort's outcomes. The final paper reports the correction and does not pool
any results across the three source versions.

The first corrected-geometry technical pilot failed before its horizon because
the Sionna scene-object position setter rejected a NumPy vector. The setter
input was changed to an equivalent Python three-value list before production;
the failed pilot remains excluded. Subsequent complete behavioral and
calibration pilots in a different seed block verified the geometry log marker,
valid GPU ray solves, unchanged pre-intervention trajectories, and absence of
calibration commands. The corrected environment manifest was re-frozen after
this repair and before the new production plan.
