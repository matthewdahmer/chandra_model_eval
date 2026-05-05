# chandra_model_eval — Development Handoff

This document captures everything needed to continue development without prior knowledge of the project. It covers purpose, architecture, design decisions, known issues, and outstanding work. It is not a user guide (see `README.md`) or an xija internals reference (see `REFERENCE.md`).

---

## Purpose

This package is the **data-generation backend** for a Chandra mission thermal model evaluation pipeline. The intended end-to-end flow is:

1. A cron job calls `run_all_models()` (or the `chandra-model-eval` CLI) over a rolling time window (e.g. the trailing year).
2. Output `{msid}.json.gz` files land in a configurable output directory, one per model.
3. A web application (not yet built) reads those files to render accuracy diagnostics for the 14 active thermal models.
4. The output files are also intended for direct LLM consumption — the `analytics` and `dwell_table` sections were specifically designed to be self-contained and interpretable without further computation.

The package does **not** include plotting, a web server, or any visualisation. It is a pure data-generation library.

---

## Repository layout

```
chandra_model_eval/              ← project root (git repo)
├── chandra_model_eval/          ← Python package
│   ├── __init__.py              ← re-exports all public names from models.py
│   ├── __main__.py              ← CLI entry point (chandra-model-eval command)
│   ├── models.py                ← ChandraModel base, 14 model classes, export pipeline
│   └── calc_model_data.py       ← pitch binning, dwell table, analytics computations
├── analyze_model_error.py       ← standalone script, superseded (see below)
├── pyproject.toml               ← package install config (pip install -e .)
├── README.md                    ← user-facing API and output format reference
├── REFERENCE.md                 ← xija framework internals
├── file_structure.md            ← annotated reference for every key in the JSON output
└── HANDOFF.md                   ← this file
```

`calc_model_data.py` is **not publicly exported** from `__init__.py`. Its functions are internal to the pipeline; users go through `evaluate()` and `export_result()`.

`analyze_model_error.py` in the project root is a standalone analysis script written before the analytics pipeline was built into the package. It contains six matplotlib plotting functions that are not yet part of the package. **Do not integrate or delete without a deliberate decision** — it is kept as a reference for a future plotting module.

---

## Environment and installation

All key dependencies (`xija`, `cheta`, `kadi`, `cxotime`) are not on PyPI. They are installed through a **Ska conda environment**. The active environment name is stored in `pyproject.toml` and is the single place to update it when it changes:

```toml
[tool.chandra_model_eval]
conda_env = "ska3-dev"
```

Read the name programmatically:

```bash
python3 -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['tool']['chandra_model_eval']['conda_env'])"
```

Activate before running anything:

```bash
conda activate $(python3 -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['tool']['chandra_model_eval']['conda_env'])")
```

Install the package itself in editable mode from the repo root (re-run after any change to `pyproject.toml` or package structure):

```bash
pip install -e ~/AXAFLIB/chandra_model_eval
```

Verify the environment is set up correctly:

```python
import xija, cheta, kadi, cxotime
from chandra_model_eval import MODELS, run_all_models
print(list(MODELS.keys()))
# expected: ['aacccdpt', '1deamzt', '1dpamzt', 'fptemp', '1pdeaat',
#            'pftank2t', '4rt700t', 'tpc_fsse', 'tpcm_rw5', 'pline03t',
#            'pline04t', 'pm1thv2t', 'pm2thv1t', '2ceahvpt']
```

---

## Local paths

| Resource | Path |
|---|---|
| This repo | `~/AXAFLIB/chandra_model_eval/` |
| chandra_models repo | `~/AXAFLIB/chandra_models/` |
| Model spec files | `~/AXAFLIB/chandra_models/chandra_models/xija/{model}/` |
| chandra_models release (as of last update) | `3.73.1` |
| Example output directory | `~/AXAFLIB/chandra_model_eval/data/model_eval/` |

`MODEL_SPECS` paths are relative to the `models_root` argument and already include the inner `chandra_models/` subdirectory prefix:

```python
MODEL_SPECS['1dpamzt'] == 'chandra_models/xija/dpa/dpa_spec.json'
# with models_root='~/AXAFLIB/chandra_models', resolves to:
# ~/AXAFLIB/chandra_models/chandra_models/xija/dpa/dpa_spec.json
```

---

## The two operating modes

### `run(tstart, tstop)` — planning mode

Sets **all** `model_init` values including the primary MSID node, then integrates the ODE forward from the limit temperature without fetching any telemetry. Returns the raw `xija.ThermalModel` object. Used for forward planning.

### `evaluate(tstart, tstop)` — evaluation mode

Leaves the primary MSID node unset so xija fetches real telemetry from the engineering archive (`cheta`). Pseudo-nodes are still initialized from `model_init`. Returns a `ModelResult` dataclass.

**Burn-in:** `evaluate()` starts the xija run 7 days before the requested `tstart` (using the same `model_init` initial conditions) so that initial-condition transients wash out before the window of interest. After `model.calc()`, the burn-in period is discarded with a simple boolean mask (`model.times >= tstart_cxc`). All downstream arrays, statistics, and analytics only contain data within the requested window. The 7-day constant is `BURN_IN_DAYS = 7` at the top of `evaluate()`.

The key distinction between the modes:

```python
# run() — sets all nodes including primary MSID
for key, val in self.model_init.items():
    model.comp[key].set_data(val)

# evaluate() — skips primary MSID so xija fetches telemetry
for key, val in self.model_init.items():
    if key != self.msid:
        model.comp[key].set_data(val)
```

---

## Data pipeline inside `evaluate()`

```
evaluate(tstart, tstop)
│
├── _build(tstart_burn, tstop)       ← build xija model starting 7 days early
├── set_data() on pseudo-nodes       ← initialise thermal masses, power nodes
├── model.make() / model.calc()      ← run xija integrator
├── clip arrays to tstart..tstop     ← discard burn-in period
│
├── _spec_info()                     ← MD5, GitHub URL, git release tag
│
├── _compute_pitch_data(...)         ← all pitch/dwell/analytics work
│   ├── get_pitch_midpoints(model)   ← extract pitch grid from solarheat params
│   ├── get_npnt_state_data(...)     ← fetch kadi NPNT states for window
│   ├── bin_data_by_pitch(...)       ← split telem/error into per-dwell traces by pitch
│   ├── compute_pitch_bin_statistics(...)  ← per-bin stats (telem + error)
│   ├── fetch dist_satearth         ← cheta archive, one value per dwell start
│   ├── fetch extra_dwell_msids      ← cheta archive, median per dwell (model-specific)
│   ├── build_dwell_table(...)       ← compact per-dwell table
│   └── compute_analytics(...)       ← monthly, error_by_temperature, period_comparison
│
├── _compute_solar_params(model)     ← extract P and dP solarheat values per component
├── _compute_dpa_power(model)        ← extract dpa_power lookup table if present
├── collect inputs from model.comp   ← dvals for every component aligned to model.times
│
└── ModelResult(...)                 ← dataclass holding all outputs
```

Each step after the clip is wrapped in `try/except` and falls back to empty structures on failure, logging a WARNING. This means a kadi or cheta outage only suppresses the pitch/analytics section — the time-series output is still written.

---

## Module responsibilities

### `models.py`

Owns everything xija-facing:

- `MODEL_SPECS` dict: MSID → relative spec path
- `ModelResult` dataclass: typed container for all outputs; key fields: `times`, `predicted`, `observed`, `plist`, `metadata`, `telem_segments`, `err_segments`, `segment_norm`, `telem_bounds`, `pitch_bin_statistics`, `dwell_table`, `analytics`, `solar_heat_components`, `dpa_power`, `inputs`
- `ChandraModel` base class: `run()`, `evaluate()`, `_build()`, `_spec_info()`, `_read_spec_limits()`, `_default_limit()`, `_compute_pitch_data()`, `_compute_solar_params()`, `_compute_dpa_power()`
- `ChandraModel.extra_dwell_msids`: class-level list of additional cheta MSIDs to fetch per-dwell medians for; `[]` in base class, overridden in model-specific subclasses
- 14 model subclasses (one per MSID)
- `MODELS` dict: MSID → model class (registry for iteration)
- `export_result(result, path)`: serializes a `ModelResult` to `.json.gz`
- `run_all_models(tstart, tstop, outdir, models_root, ...)`: cron-friendly batch runner

### `calc_model_data.py`

Pure data-processing; no xija interface, no file I/O. Takes numpy arrays and returns serialisable Python structures. Functions:

| Function | Purpose |
|---|---|
| `get_npnt_state_data(tstart, tstop)` | Fetch kadi NPNT state intervals |
| `get_pitch_midpoints(model)` | Extract pitch bin boundaries from solarheat parameters |
| `get_solarheat_components(model)` | Extract solarheat P and dP parameter values, one dict per component |
| `get_dpa_power_params(model)` | Extract dpa_power lookup table (pattern → watts), mult, bias |
| `bin_data_by_pitch(...)` | Split telem/error arrays into per-dwell traces by pitch bin |
| `compute_pitch_bin_statistics(...)` | Compute per-bin summary stats for telem and error |
| `build_dwell_table(...)` | Build compact per-dwell analytics table |
| `compute_analytics(...)` | Compute near_limit_threshold, monthly, error_by_temperature, period_comparison |
| `_seg_stats(seg_list)` | Private: pool statistics across a list of segments |
| `_cxc_to_dt(cxc_secs)` | Private: CXC seconds → UTC datetime array |

### `__main__.py`

CLI entry point. Parses arguments, resolves the time window, configures logging, calls `run_all_models()`, and exits with code 1 on any failure. No business logic lives here.

---

## CLI reference

```
chandra-model-eval [tstart tstop] outdir models_root [options]
```

| Argument / Flag | Description |
|---|---|
| `tstart`, `tstop` | Explicit start/stop times (any CXC-accepted format) |
| `outdir` | Output directory for `.json.gz` files |
| `models_root` | Root of the `chandra_models` repo checkout |
| `--trailing-days N` | Rolling window of N days ending at UTC now (or `--end-date`) |
| `--end-date DATE` | Override the end of the rolling window (e.g. when data lags current time) |
| `--limit-override MSID=VALUE` | Override planning limit; repeatable; required for `pm2thv1t` |
| `--model MSID` | Run only this single model (mutually exclusive with `--models`) |
| `--spec PATH` | Path to a custom spec JSON for the model given by `--model`; only valid with `--model` |
| `--models MSID ...` | Run only the listed models (default: all 14) |
| `--log-level` | DEBUG / INFO (default) / WARNING / ERROR |
| `--log-file PATH` | Write log to file instead of stderr |

**`--spec`** is useful for evaluating a candidate spec before committing it to the `chandra_models` repo. It overrides only the spec path; the model class, limit, and all other behaviour remain unchanged.

Typical cron invocation (all 14 models; note the required `pm2thv1t` override):

```bash
chandra-model-eval --trailing-days 365 --end-date 2025:180 \
    /data/model_eval ~/AXAFLIB/chandra_models \
    --limit-override pm2thv1t=227.5 \
    --log-file /data/model_eval/run.log
```

Single-model test run with a candidate spec:

```bash
chandra-model-eval --trailing-days 30 /data/model_eval ~/AXAFLIB/chandra_models \
    --model 1dpamzt --spec /tmp/dpa_spec_candidate.json
```

`--end-date` was added specifically because the telemetry archive may lag current time; using it avoids the window extending into a period with no data.

---

## `pm2thv1t` always requires a limit override

`pm2thv1t_spec_matlab.json` has no `limits` block. `_default_limit()` always raises:

```
ValueError: ModelPM2THV1T: no 'planning.warning.high' in spec limits for 'pm2thv1t'; pass limit explicitly.
```

The correct operational value is approximately **227.5 °F**. Every invocation that includes `pm2thv1t` must supply:

```bash
--limit-override pm2thv1t=227.5
```

Any CLI invocation that processes all 14 models — cron jobs, batch runs, documentation examples — **must** include this flag. Omitting it causes `pm2thv1t` to fail and be recorded in the `failed` dict; the other 13 models still complete.

---

## Output file format

Each run produces `{outdir}/{msid}.json.gz` — a gzip-compressed compact JSON file. See `file_structure.md` for a fully annotated reference of every key. The top-level structure is:

```
{msid}.json.gz
├── msid, limit, limit_type, all_limits, units       ← identity
├── spec_md5, spec_github_url, spec_github_release   ← provenance
├── datestart, datestop, times, predicted,
│   observed, residuals, stats, violations            ← time series + global stats
├── pitch_analysis/
│   ├── plist, telem_bounds
│   ├── metadata, telem_segments, err_segments,
│   │   segment_norm                                  ← per-dwell traces
│   └── pitch_bin_statistics/                         ← per-bin telem + error summary
├── dwell_table/                                      ← compact per-dwell table
├── analytics/                                        ← pre-computed diagnostics
├── solar_heat_components                             ← P and dP solarheat parameters per component
├── dpa_power                                         ← DPA power lookup table (empty dict if unused)
└── inputs/                                           ← dvals for every xija component, aligned to times
```

Only data within the requested `tstart`/`tstop` window is written. The 7-day burn-in is never written to disk.

---

## Validating output

Quick sanity check after a run:

```python
import gzip, json
with gzip.open('data/model_eval/1dpamzt.json.gz') as f:
    d = json.load(f)

# Check these first — if any are None or empty the run had a problem
print(d['msid'], d['limit'], d['units'])
print(d['stats'])                        # should have mean, std, n
print(d['analytics'].get('near_limit_threshold'))  # None means pitch data failed
print(len(d['dwell_table'].get('tstart', [])), 'dwells')
print(d['violations']['count'], 'violations')
```

Signs of a broken run:

- **`stats` is `{}`** — xija integration itself failed; check the log for the model.
- **`analytics` is `{}`** — kadi or cheta was unavailable; pitch data computation fell back to empty. The time-series fields (`times`, `predicted`, `observed`) should still be populated.
- **`predicted` and `observed` arrays are all `null`** — the xija run failed entirely.
- **`dwell_table` is empty or has no `dist_satearth` key** — the cheta fetch for `dist_satearth` failed for the whole model run; other dwell columns are still valid.

---

## Performance

A single model over a 365-day window takes roughly **30–90 seconds** depending on the model. The full 14-model batch takes **10–20 minutes**. The 7-day burn-in adds one extra xija run per model but is small relative to total time.

Do not run `run_all_models()` interactively for quick testing — run a single model with a short window instead:

```python
from chandra_model_eval import MODELS
import os

models_root = os.path.expanduser('~/AXAFLIB/chandra_models')
spec = os.path.join(models_root, 'chandra_models/xija/dpa/dpa_spec.json')
m = MODELS['1dpamzt'](model_spec=spec)
result = m.evaluate('2025:300', '2025:330')   # short window for quick testing
print(result.stats())
```

---

## External dependency diagnostics

### xija unavailable

Import fails at module load time — no graceful fallback. The error will appear before any model runs. Check that the correct conda environment is activated (see `pyproject.toml` `[tool.chandra_model_eval].conda_env`).

```
ModuleNotFoundError: No module named 'xija'
```

### kadi unavailable

`get_npnt_state_data()` raises inside `_compute_pitch_data()`. The outer `try/except` catches it and logs:

```
WARNING chandra_model_eval.models: pitch data computation failed for <msid>: <error>
```

The JSON output will have empty `pitch_analysis`, `dwell_table`, and `analytics`, but `times`, `predicted`, `observed`, `stats`, and `violations` are still written correctly.

### cheta unavailable

xija raises during `model.calc()` when it tries to fetch telemetry for the primary MSID node. This causes `evaluate()` to raise, which `run_all_models()` catches and logs:

```
ERROR chandra_model_eval.models: failed <msid>: <error>
```

The model is added to the `failed` dict; other models continue running. The output file for the failed model is not written (or will be the previous run's file if one exists).

### cheta available but dist_satearth fetch fails

`_compute_pitch_data()` catches per-MSID fetch failures independently. The dwell table is still built; the `dist_satearth` key is simply absent from it. Logged at WARNING:

```
WARNING chandra_model_eval.models: dist_satearth fetch failed for <msid>: <error>
```

Same behaviour for per-model extra MSIDs (e.g. `2imonst` for `2ceahvpt`): a failed fetch skips that column for that dwell.

### Archive data lag

If the stop time extends into a period with no telemetry, cheta raises during xija model evaluation. Use `--end-date` to clamp the window to the last date with available data.

---

## Design decisions

### Two-module structure

All xija interface code lives in `models.py`. All pitch/dwell/analytics computation lives in `calc_model_data.py`. The separation is intentional: `models.py` owns the xija model lifecycle and the public API; `calc_model_data.py` is a pure data-processing module. This keeps the two concerns independently testable — `calc_model_data.py` functions can be tested with synthetic numpy arrays and no xija or kadi dependency.

### Burn-in via early start

Initial conditions are set at the planning limit temperature (a worst-case value), which introduces significant transient error in the first hours of the prediction. Rather than attempting to find a better initial condition, the model simply starts 7 days early and clips the output. This is robust and requires no per-model tuning. The downside is that each model run fetches 7 extra days of telemetry and runs xija for a slightly longer window, adding a few seconds of runtime per model.

### Error-first design philosophy

The analytics pipeline prioritises understanding errors near the planning limit:

1. **Bias at high/low temperature** (`error_by_temperature`) — reveals whether the model is systematically wrong when the spacecraft is near the limit. The most operationally relevant metric.
2. **Temporal drift** (`period_comparison`, `monthly`) — detects whether the model is degrading over time, indicating the spec needs updating.
3. **Intra-dwell drift** (`segment_drift_mean` in `pitch_bin_statistics`) — captures whether the model consistently drifts within a dwell at a given pitch, pointing to incorrect solarheat parameters.
4. **Overall statistics** (`stats`) — provides global bias and scatter as context, but a small global bias that concentrates near the limit is more dangerous than a large bias that occurs only at benign temperatures.

### `near_limit_threshold` derivation

"Near-limit" dwells are defined as those in the top third (max-limit models) or bottom third (min-limit models) of the **observed temperature range** (`telem_bounds`), not relative to the limit value itself. This adapts automatically to each model's actual operating range.

**Known limitation:** if the model consistently operates far from its limit (e.g. due to operational margin), the "near-limit" threshold may not actually be near the limit. A future improvement would be to compute the threshold as a fixed engineering margin below/above the limit (e.g. 1.5 °C below the planning limit) rather than from the observed range.

### `inputs` collection

After `model.calc()` and the burn-in clip, `evaluate()` iterates `model.comp` and saves `dvals` for every component whose array length matches `len(model.times)`:

```python
for comp_name, c in model.comp.items():
    dv = c.dvals
    if dv is not None and len(dv) == n_times:
        inputs[comp_name] = dv[clip].copy()
```

Each component's `dvals` represents the data values that xija used for that component during the run — fetched telemetry for real MSIDs (e.g. `pitch`, `roll`, `sim_z`, `fep_count`), set values for pseudo-nodes (e.g. `dpa0`), and computed values for model components (e.g. `solarheat__dpa0`, `dpa_power`). All arrays share the same time axis as `times`, `predicted`, and `observed`.

Serialization in `export_result()` dispatches by dtype: float arrays use NaN → `null` conversion; bool arrays are cast to `int8` (0/1); integer arrays are passed through directly. Failed per-component `dvals` accesses are silently skipped (bare `except`), so a component that raises on `.dvals` access simply does not appear in `inputs` rather than aborting the run.

### `extra_dwell_msids` class attribute

Models that need additional per-dwell telemetry columns override the `extra_dwell_msids` class attribute on `ChandraModel` (base value: `[]`). The base class `_compute_pitch_data()` reads this list, fetches each MSID from the cheta archive, and passes the data to `build_dwell_table()`, which computes the median over each dwell interval and adds it as a column. This pattern requires zero changes to `ChandraModel` base class logic to support a new model-specific column.

### `run_all_models()` never raises

All per-model exceptions are caught, logged at ERROR level, and accumulated in the `failed` dict. One broken model does not abort the rest. This is intentional for cron use where partial output is better than no output.

### `_compute_pitch_data()` never raises

Wrapped in `try/except` with a fallback to empty structures. If kadi or cheta is unavailable, the time-series data (`times`, `predicted`, `observed`, `residuals`, `stats`, `violations`) is still exported correctly. Only `pitch_analysis`, `dwell_table`, and `analytics` will be empty.

### Pseudo-node identification

xija has no API flag to distinguish pseudo-nodes (thermal masses, e.g. `dpa0`, `aca0`) from the prediction target. Each model's `model_init` dict was populated by reading the spec JSON manually. If adding a new model, inspect the spec for `Node` components whose `msid` field does not correspond to a real engineering telemetry MSID — those are pseudo-nodes and need explicit initialisation.

### `_read_spec_limits()` and `_default_limit()`

`_read_spec_limits()` reads `spec['limits'][self.msid]` — the key is the MSID string exactly. Returns all entries except `'unit'` as `all_limits` and the `'unit'` value as `units` (defaulting to `'degC'`).

`_default_limit()` constructs `f'planning.warning.{"high" if limit_type == "max" else "low"}'`. Note the suffix is `high`/`low`, not `max`/`min`. An earlier version of this code had this wrong and produced the error `no 'planning.warning.max' found in spec limits`.

### GitHub URL hardcoding

`_spec_info()` hardcodes `https://github.com/sot/chandra_models/blob/{commit}/{rel_path}`. If spec files from a fork or local-only checkout are used, the URL will point to the wrong place. The entire git block is wrapped in `except Exception: pass`, so it silently degrades to `None` in environments without git or outside a git repo.

### Lazy `cxotime` import

`cxotime` is imported lazily inside `evaluate()`, `build_dwell_table()`, `compute_analytics()`, and `export_result()` rather than at module top level. This avoids a hard import-time dependency for users who only use `run()`.

### `segment_norm` colour scaling

Each dwell segment is assigned a normalised starting temperature in `[0, 1]` against `telem_bounds`. This allows a web UI or plotting code to colour-code traces consistently across all pitch bins using the same colour scale, without needing to recompute the global range.

### Dwell filter: >1 hour, NPNT only

`bin_data_by_pitch()` skips dwells shorter than 3600 seconds and only processes NPNT (normal pointing) states. This is enforced by the condition `(s['tstop'] - s['tstart']) > 3600` inside the loop. Short dwells don't produce useful thermal signatures and add noise to per-bin statistics.

---

## Adding a new model

Follow these steps in order:

1. **Add to `MODEL_SPECS`** in `models.py` (`models.py:29`). Key is the MSID string; value is the spec path relative to `models_root`, including the inner `chandra_models/` prefix (e.g. `'chandra_models/xija/newmodel/newmodel_spec.json'`).

2. **Create the subclass** following the pattern of any existing class (`models.py:389+`). Required fields set in `__init__`: `msid`, `limit_type`, `model_spec`, `all_limits` (from `_read_spec_limits()`), `units` (from `_read_spec_limits()`), `limit`, `model_init`.

3. **Identify pseudo-nodes** by reading the spec JSON. Look for `"Node"` components in the `"comps"` list whose `"msid"` field does not correspond to a real engineering telemetry MSID (e.g. `"dpa0"`, `"aca0"`, `"cea0"`). Every pseudo-node must appear in `model_init` with a sensible initial value (typically the limit temperature).

4. **Handle the no-limits case** — if the spec has no `limits` block for the MSID, calling `_default_limit()` will raise. Do not call it; require an explicit `limit` argument at construction time (see `ModelPM2THV1T` at `models.py:534`).

5. **Set `limit_type='min'`** for cold limits (propulsion line models) — this inverts the violation check and the near-limit analytics threshold. All other models use `limit_type='max'`.

6. **Override `extra_dwell_msids`** if the model needs additional per-dwell cheta telemetry columns in the dwell table (see `Model2CEAHVPT` at `models.py:547`). Otherwise the base class empty list is used.

7. **Register in `MODELS`** at `models.py:568`.

8. **Test with a short window** before running a full-year batch.

---

## Special cases per model

| Model | What makes it unusual |
|---|---|
| `pm2thv1t` | Spec file (`pm2thv1t_spec_matlab.json`) has no `limits` block. `_default_limit()` always raises; `limit` must always be passed explicitly. The correct operational value is approximately 227.5 °F. The `--limit-override pm2thv1t=227.5` flag must always be included in any CLI invocation that includes this model. |
| `fptemp` | `all_limits` contains many ACIS-configuration-dependent data-quality limits beyond `planning.warning.high`. `1cbat` and `sim_px` in `model_init` are fixed at ACIS-S typical values (`-55.0` and `110.0`) — not fetched from telemetry. These affect the focal-plane temperature model accuracy for non-ACIS-S configurations. |
| `pline03t`, `pline04t` | `limit_type='min'` — cold limits. A violation is `predicted < limit`. The near-limit analytics threshold is the bottom third of the observed range. Error sign interpretation is reversed: positive error (observed > predicted) means the model overestimates warming. |
| `2ceahvpt` | `eclipse=False` and `dpa_power=0.0` set as fixed values in `model_init`. Eclipse handling is disabled; this is intentional per the model design. Overrides `extra_dwell_msids` with six HRC-specific cheta MSIDs: `2imonst`, `2sponst`, `2s2onst`, `224pcast`, `215pcast`, `aoeclips`. All six return string-valued `.vals` from cheta; `.raw_vals` (int8) is used instead. `224pcast` and `215pcast` raw values are inverted (`1 - raw_vals`) before storage because their encoding is backwards. Per-dwell medians of the resulting 0/1 values are stored as extra `dwell_table` columns. |
| `1deamzt`, `1dpamzt`, `1pdeaat` | Include `dpa_power` pseudo-node initialised to `0.0`. The actual DPA power is fetched from telemetry by xija during evaluation. These three models also have non-empty `dpa_power` in the JSON output. |
| `aacccdpt` | The `aca0` pseudo-node represents the ACA thermal mass. |
| `1pdeaat` | The `pin1at` naming is a pseudo-node for the PSMC input temperature, not the primary MSID. `1pdeaat` is the prediction target. |

---

## Known bugs

### Fallback return value in `_compute_pitch_data()`

When `plist` is empty (model has no solarheat parameters), the fallback path returns:

```python
_empty = ({}, {}, {}, {}, {}, {}, {})   # 7 dicts
return [], *_empty, telem_bounds         # telem_bounds lands in wrong position
```

The unpack in `evaluate()` expects 9 values in order:
```
plist, metadata, telem_segments, err_segments, segment_norm,
telem_bounds, pitch_bin_statistics, dwell_table, analytics
```

The current fallback puts `{}` in `telem_bounds` and puts the actual `telem_bounds` tuple where `pitch_bin_statistics` is expected. This bug only activates when a model has no solarheat parameters (which none of the current 14 do, but would affect any new model added without solarheat components). Fix:

```python
# Current (wrong):
return [], *_empty, telem_bounds    # telem_bounds in wrong slot

# Correct:
return [], {}, {}, {}, {}, telem_bounds, {}, {}, {}
```

The `except` clause at the bottom of `_compute_pitch_data()` has the **same** bug — the same fix applies to both paths. Both are low risk because neither activates for the current 14 models under normal operations.

### `bin_data_by_pitch()` uses `s.dtype` outside loop scope

At the end of the inner loop in `bin_data_by_pitch()`:

```python
metadata[num] = np.array(metadata[num], dtype=s.dtype)
```

`s` is the last value assigned in the `for s in state_data[pind==num]` loop. If that loop runs zero iterations, `s` is undefined and this raises `NameError`. This would only occur if `state_data[pind==num]` is non-empty but all entries are skipped by the `any(tind) & ...` condition. Currently unreachable in practice but is a latent bug.

---

## What still needs to be built

### Must-fix before production use

- **Fallback return value bug** in `_compute_pitch_data()` — two-line fix described above in both the empty-plist path and the `except` clause.
- **`s.dtype` scope bug** in `bin_data_by_pitch()` — guard the `np.array(..., dtype=s.dtype)` call against the case where the inner loop body never executed.

### Infrastructure

- **Cron job** — no systemd timer or crontab entry exists. The typical invocation is:
  ```bash
  chandra-model-eval --trailing-days 365 --end-date $(date -u +%Y:%j) \
      /data/model_eval ~/AXAFLIB/chandra_models \
      --limit-override pm2thv1t=227.5 \
      --log-file /data/model_eval/run.log
  ```
  Consider a systemd timer or crontab entry running weekly or nightly.

- **Test suite** — no tests exist. Priority targets:
  - `_read_spec_limits()` and `_default_limit()` — can be tested with a minimal synthetic JSON spec
  - `stats()` and `violations()` on `ModelResult` — pure numpy, easy to unit test
  - `build_dwell_table()` and `compute_analytics()` — test with synthetic structured arrays
  - `export_result()` — round-trip test: export then decompress and compare keys
  - `evaluate()` — mock `xija.ThermalModel` by injecting a fake `comp` object with `mvals` and `dvals` arrays; mock `kadi` states for pitch binning

### Analytics improvements

- **`near_limit_threshold` refinement** — the current derivation (top/bottom third of observed range) can miss the limit if the model runs well within its margin. A better approach would be `limit - margin` where `margin` is derived from recent operational history or a fixed engineering value (e.g. 1.5 °C below the limit).
- **Rolling window trend** — `period_comparison` uses a fixed 2/3 early / 1/3 recent split by dwell count. A calendar-based split (e.g. last 90 days vs prior) would be more interpretable and easier to explain to operators.
- **`err_end` reliability** — `err_end` takes the mean of the last 20% of a dwell. For very short qualifying dwells (just over 1 hour), the last 20% is only ~12 minutes. Consider a minimum absolute duration (e.g. last 20 minutes, not last 20%) to make this more robust.

### Application layer

- **Web app** — the intended consumer of the `.json.gz` files. The `dwell_table` and `analytics` sections were specifically structured to be loaded and displayed without further computation. The `pitch_analysis` section is more suited to an interactive trace viewer where individual dwells can be selected.
- **Plotting module** — `analyze_model_error.py` in the project root contains six matplotlib figures (time-series overview, monthly trend, error-by-temperature, pitch-bin overlays, period comparison, scatter plots) that are not yet integrated into the package. These could become a `plot.py` submodule or be moved into the web app frontend.

---

## Logger and observability

All progress and error messages go to the `chandra_model_eval.models` logger (`logging.getLogger(__name__)` in `models.py`). Pitch computation warnings, dist_satearth fetch failures, and extra MSID fetch failures all use the same logger at WARNING level. Configure a handler before calling `run_all_models()`:

```python
import logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s: %(message)s',
    filename='/data/model_eval/run.log',
)
```

The CLI does this automatically based on `--log-level` and `--log-file`.

A normal run looks like:
```
2025-06-01T12:00:01 INFO chandra_model_eval.models: running 1dpamzt (2024:152 to 2025:152)
2025-06-01T12:01:15 INFO chandra_model_eval.models: wrote /data/model_eval/1dpamzt.json.gz
```

A kadi failure looks like:
```
2025-06-01T12:01:15 WARNING chandra_model_eval.models: pitch data computation failed for 1dpamzt: ...
2025-06-01T12:01:15 INFO chandra_model_eval.models: wrote /data/model_eval/1dpamzt.json.gz
```
(The file is still written — only the `pitch_analysis`, `dwell_table`, and `analytics` sections are empty.)

A cheta failure looks like:
```
2025-06-01T12:01:15 ERROR chandra_model_eval.models: failed 1dpamzt: ...
```
(The file is not written for this model; other models continue.)

---

## Companion documents

| File | Contents |
|---|---|
| `README.md` | User-facing API reference, output format overview, CLI usage |
| `REFERENCE.md` | xija framework internals (model components, parameter types, etc.) |
| `file_structure.md` | Fully annotated reference for every key in the `.json.gz` output, including interpretation guidance |
| `HANDOFF.md` | This file |
