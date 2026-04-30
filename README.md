# chandra_model_eval

Xija thermal model wrappers for Chandra mission planning and evaluation. Provides a consistent interface for running and evaluating the 14 active thermal models used to plan Chandra observations.

## Dependencies

- `xija` — Chandra thermal modeling framework
- `numpy`
- `cxotime` — Chandra time conversion (used in `export_result`)
- `cheta` — engineering telemetry archive (required by `evaluate()` to fetch observed data)
- `kadi` — commanded states archive (used internally by xija for some models)

## Installation

The package is installed directly from the repository root:

```bash
pip install -e .
```

Once installed, import from the package name:

```python
from chandra_model_eval import Model1DPAMZT, MODELS, MODEL_SPECS, export_result, run_all_models
```

## Model spec files

Model specs are JSON files from the [`chandra_models`](https://github.com/sot/chandra_models) repository. Each class takes an explicit `model_spec` path so you can point to any version of the specs on disk. The `MODEL_SPECS` dict lists the relative paths within that repo for reference:

```python
from chandra_model_eval import MODEL_SPECS
# e.g. MODEL_SPECS['1dpamzt'] == 'chandra_models/xija/dpa/dpa_spec.json'
```

A typical absolute path:

```text
/proj/sot/ska/data/chandra_models/chandra_models/xija/dpa/dpa_spec.json
```

## Available models

| Class | MSID | Limit type | Description |
|---|---|---|---|
| `ModelAACCCDPT` | `aacccdpt` | max | ACA CCD temperature |
| `Model1DEAMZT` | `1deamzt` | max | DEA temperature |
| `Model1DPAMZT` | `1dpamzt` | max | DPA temperature |
| `ModelFPTEMP` | `fptemp` | max | ACIS focal plane temperature |
| `Model1PDEAAT` | `1pdeaat` | max | PSMC temperature |
| `ModelPFTANK2T` | `pftank2t` | max | Propulsion fuel tank temperature |
| `Model4RT700T` | `4rt700t` | max | Forward bulkhead temperature |
| `ModelTPC_FSSE` | `tpc_fsse` | max | TPC FSSE temperature |
| `ModelTPCM_RW5` | `tpcm_rw5` | max | RWA-5 motor controller temperature |
| `ModelPLINE03T` | `pline03t` | **min** | Propulsion line temperature (cold limit) |
| `ModelPLINE04T` | `pline04t` | **min** | Propulsion line temperature (cold limit) |
| `ModelPM1THV2T` | `pm1thv2t` | max | MUPS-1B valve temperature |
| `ModelPM2THV1T` | `pm2thv1t` | max | MUPS-2A valve temperature |
| `Model2CEAHVPT` | `2ceahvpt` | max | HRC CEA temperature |

## Instantiating a model

Every class takes one required argument and one optional keyword argument:

```python
ModelXXXXX(model_spec, limit=None)
```

| Argument | Type | Description |
|---|---|---|
| `model_spec` | str | Absolute path to the model spec JSON file |
| `limit` | float or None | Planning warning limit. If `None` (default), the `planning.warning.high` or `planning.warning.low` value is read from the model spec automatically. |

```python
from chandra_model_eval import Model1DPAMZT

# Use the limit defined in the model spec (planning.warning.high = 38.5)
model = Model1DPAMZT(
    model_spec='/proj/sot/ska/data/chandra_models/chandra_models/xija/dpa/dpa_spec.json',
)

# Or override with an explicit value
model = Model1DPAMZT(
    model_spec='/proj/sot/ska/data/chandra_models/chandra_models/xija/dpa/dpa_spec.json',
    limit=37.5,
)
```

After instantiation, every model exposes these attributes read from the spec:

| Attribute | Type | Description |
|---|---|---|
| `limit` | float | Active planning warning limit |
| `all_limits` | dict | Every limit entry from the spec's limits block (all limit types, excluding the `unit` key) |
| `units` | str | Temperature unit from the spec (`'degC'` or `'degF'`) |

```python
model.limit
# 38.5

model.units
# 'degC'

model.all_limits
# {'planning.warning.high': 38.5, 'planning.caution.low': 13.0,
#  'odb.caution.high': 40.5, 'odb.warning.high': 42.5}
```

For models with multiple planning warning limits, all are present in `all_limits`. For example, `ModelFPTEMP` exposes the full set of ACIS configuration-dependent data quality limits:

```python
from chandra_model_eval import ModelFPTEMP

m = ModelFPTEMP(model_spec=spec)
m.limit
# -86.0  (planning.warning.high)

m.all_limits
# {
#   'planning.warning.high':                    -86.0,
#   'planning.data_quality.high.acis_0':       -111.0,
#   'planning.data_quality.high.acis_1':       -108.0,
#   'planning.data_quality.high.acis_2':       -105.0,
#   'planning.data_quality.high.acisi':        -112.0,
#   'planning.data_quality.high.aciss':        -111.0,
#   'planning.data_quality.high.aciss_hot':    -109.0,
#   'planning.data_quality.high.aciss_hot_b':  -105.0,
#   'planning.data_quality.high.cold_ecs':     -118.2,
#   'planning.data_quality.high.grating_0':    -109.0,
#   'planning.data_quality.high.grating_1':    -105.0,
#   'safety.caution.high':                      -80.0,
# }
```

> **Note:** `ModelPM2THV1T` (`pm2thv1t`) has no limits defined in its model spec. An explicit `limit` value must always be passed for this model.

## Two operating modes

### `run(tstart, tstop)` — planning mode

Sets **all** `model_init` values (including the primary MSID node) to the specified initial conditions before integrating. Starts the ODE from the limit temperature; does not fetch telemetry. Returns the raw `xija.ThermalModel` object.

Use this when you want to propagate a forward prediction from a known starting state.

```python
xija_model = model.run('2025:001', '2025:010')

predicted = xija_model.comp['1dpamzt'].mvals   # shape (n_times,)
times     = xija_model.times                   # CXC seconds
```

### `evaluate(tstart, tstop)` — evaluation mode

Leaves the primary MSID node unset so xija fetches real telemetry from the engineering archive. Pseudo-nodes (thermal masses, power nodes) are still initialized from `model_init`. Returns a `ModelResult`.

Use this to assess how well the model matches historical telemetry.

```python
result = model.evaluate('2025:001', '2025:090')
```

Internally the model is run starting 7 days before `tstart` so that errors from the initial conditions have time to wash out. The 7-day warm-up period is discarded before the `ModelResult` is returned — all arrays, statistics, and analytics contain only data within the requested `tstart`/`tstop` window.

## Working with `ModelResult`

`evaluate()` returns a `ModelResult` dataclass with the following fields:

| Field | Type | Description |
|---|---|---|
| `msid` | str | MSID name |
| `times` | ndarray | Model time array in CXC seconds, clipped to the requested window |
| `predicted` | ndarray | Model-predicted temperatures, clipped to the requested window |
| `observed` | ndarray | Telemetry temperatures, clipped to the requested window |
| `limit` | float | Active planning warning limit |
| `limit_type` | str | `'max'` or `'min'` |
| `all_limits` | dict | All limit entries from the spec (same as on the model instance) |
| `units` | str | Temperature unit (`'degC'` or `'degF'`) |
| `spec_md5` | str | MD5 hex digest of the model spec file on disk |
| `spec_github_url` | str or None | Permanent GitHub blob URL using the commit SHA; `None` if git is unavailable |
| `spec_github_release` | str or None | Exact git tag if on a release, otherwise `git describe --tags` output; `None` if no tags reachable |

### `.residuals`

```python
resids = result.residuals   # observed - predicted, shape (n_times,)
```

### `.stats()`

Returns a dict of residual statistics with NaN values excluded:

```python
s = result.stats()
# {
#   'n':       99312,     # number of valid points
#   'mean':   -0.042,     # mean residual (bias)
#   'std':     0.381,     # standard deviation
#   'rms':     0.384,     # root mean square residual
#   'max_abs': 2.11,      # largest absolute residual
#   'p05':    -0.61,      # 5th percentile
#   'p25':    -0.24,      # 25th percentile
#   'p50':    -0.03,      # median
#   'p75':     0.18,      # 75th percentile
#   'p95':     0.57,      # 95th percentile
# }
```

### `.violations()`

Returns predicted-temperature exceedances of the limit:

```python
v = result.violations()
# {
#   'times':    array([...]),   # CXC seconds of violation timestamps
#   'values':   array([...]),   # predicted temperatures at those times
#   'count':    14,             # number of violating time steps
#   'fraction': 0.00014,        # fraction of all time steps in violation
# }
```

For `limit_type='max'`, a violation is `predicted > limit`.
For `limit_type='min'`, a violation is `predicted < limit`.

## Converting CXC times

`result.times` is in CXC seconds. To convert:

```python
from cxotime import CxoTime

t = CxoTime(result.times)
dates = t.date       # array of 'YYYY:DDD:HH:MM:SS.sss' strings
iso   = t.iso        # array of ISO 8601 strings
unix  = t.unix       # Unix timestamps (for plotting libraries)
```

## Exporting results — `export_result`

`export_result(result, path)` writes a `ModelResult` to a gzip-compressed JSON file. NaN and inf values are serialized as JSON `null`. The JSON is compact (no whitespace) before compression.

```python
from chandra_model_eval import export_result

export_result(result, '/data/model_eval/1dpamzt.json.gz')
```

See `file_structure.md` for a complete annotated reference of every key in the output file, including interpretation guidance for each field. The sections below give a structural overview.

The file contains:

### Model identity and limits

| Key | Type | Description |
|---|---|---|
| `msid` | string | MSID name |
| `limit` | number | Active planning warning limit |
| `limit_type` | string | `"max"` or `"min"` |
| `all_limits` | object | All limit entries from the spec (excluding the `unit` key) |
| `units` | string | Temperature unit (`"degC"` or `"degF"`) |

### Spec provenance

| Key | Type | Description |
|---|---|---|
| `spec_md5` | string | MD5 hex digest of the spec file on disk at run time |
| `spec_github_url` | string or null | Permanent GitHub blob URL using the commit SHA; null if git unavailable |
| `spec_github_release` | string or null | Exact git tag if on a release, otherwise `git describe --tags` output; null if no tags reachable |

### Time-series data

| Key | Type | Description |
|---|---|---|
| `datestart` | string | Chandra date string (`YYYY:DDD:HH:MM:SS.sss`) of the first time step |
| `datestop` | string | Chandra date string of the last time step |
| `times` | array of numbers | CXC seconds for every time step, spanning exactly the requested window |
| `predicted` | array of numbers/null | Model-predicted temperatures; null where not finite |
| `observed` | array of numbers/null | Telemetry temperatures; null where not finite |
| `residuals` | array of numbers/null | observed − predicted; null where not finite |

> All time-series arrays are clipped to the requested `tstart`/`tstop`. The model is run internally with a 7-day warm-up period before `tstart` to eliminate initial-condition transients; that warm-up data is discarded and does not appear in the file.

### Summary statistics

`stats` is an object with the following fields (NaN values excluded before computation):

| Key | Description |
|---|---|
| `n` | Number of valid (finite) time steps |
| `mean` | Mean residual (bias) |
| `std` | Standard deviation of residuals |
| `rms` | Root mean square residual |
| `max_abs` | Largest absolute residual |
| `p05` | 5th percentile |
| `p25` | 25th percentile |
| `p50` | Median |
| `p75` | 75th percentile |
| `p95` | 95th percentile |

### Limit violations

`violations` is an object with the following fields:

| Key | Type | Description |
|---|---|---|
| `count` | number | Number of time steps where predicted temperature exceeds the limit |
| `fraction` | number | Fraction of all time steps in violation |
| `times` | array of numbers | CXC seconds of violating time steps |
| `values` | array of numbers/null | Predicted temperatures at violating time steps |

For `limit_type = "max"` a violation is `predicted > limit`; for `limit_type = "min"` it is `predicted < limit`.

### `pitch_analysis` — pitch-binned segment traces

`pitch_analysis` groups all data derived from splitting the evaluation period into NPNT dwells by pitch angle. Dwells shorter than one hour are excluded. Bin indices (used as keys throughout) are the string representation of the zero-based index into `plist`.

| Key | Type | Description |
|---|---|---|
| `plist` | array of numbers | Pitch bin boundaries in degrees, derived from the solarheat pitch grid in the model spec |
| `telem_bounds` | array of two numbers | `[min, max]` of all observed telemetry over the evaluation period |
| `metadata` | object | Bin index → array of dwell state records. Each record contains: `datestart`, `datestop`, `tstart`, `tstop`, `pitch`, `off_nom_roll`, `ccd_count`, `fep_count`, `clocking`, `vid_board`, `pcad_mode`, `simpos`, `trans_keys`. Only NPNT dwells are included. |
| `telem_segments` | object | Bin index → array of segments. Each segment is an array of `[relative_time_seconds, temperature]` pairs for one dwell. |
| `err_segments` | object | Same structure as `telem_segments` but values are `observed − predicted` residuals. |
| `segment_norm` | object | Bin index → array of floats, one per dwell. The observed temperature at dwell start normalised to `[0, 1]` against `telem_bounds`. Used for consistent colour-scaling across traces. |
| `pitch_bin_statistics` | object | Bin index → statistics object (see below), or `null` for bins with no usable data. |

`pitch_bin_statistics` entries contain:

| Field | Type | Description |
|---|---|---|
| `n_segments` | number | Number of dwells in this bin |
| `n_points` | number | Total time steps across all segments |
| `telem` | object | Statistics for observed temperature — fields listed below |
| `error` | object | Statistics for model error (observed − predicted) — same fields |

Both `telem` and `error` sub-objects contain:

| Field | Description |
|---|---|
| `mean` | Mean value pooled across all points |
| `std` | Standard deviation pooled across all points |
| `rms` | Root mean square pooled across all points |
| `max_abs` | Largest absolute value across all points |
| `p05`–`p95` | 5th / 25th / 50th / 75th / 95th percentiles |
| `segment_mean_mean` | Mean of per-dwell means — typical level for a single dwell at this pitch |
| `segment_mean_std` | Std of per-dwell means — dwell-to-dwell consistency at this pitch |
| `segment_drift_mean` | Mean intra-dwell linear slope in units/ks. For `error`, positive means the model runs progressively colder than observed during a dwell. `null` if no dwell was long enough. |
| `segment_drift_std` | Std of intra-dwell slopes across dwells |

### `dwell_table` — per-dwell analytics

A compact table of one row per qualifying dwell (>1 hr NPNT), sorted chronologically. Stored as a dict of parallel arrays. Contains all the information needed for time-trend analysis without reading the full segment traces.

| Field | Type | Description |
|---|---|---|
| `tstart` | array of numbers | Dwell start time in CXC seconds |
| `datestart` | array of strings | Dwell start as a Chandra date string |
| `pitch` | array of numbers | Mean pitch angle for the dwell (degrees) |
| `simpos` | array of integers | SIM-Z position |
| `obs_start_temp` | array of numbers | Observed temperature at the start of the dwell |
| `obs_max_temp` | array of numbers | Maximum observed temperature during the dwell |
| `obs_mean_temp` | array of numbers | Mean observed temperature during the dwell |
| `err_mean` | array of numbers | Mean model error (observed − predicted) over the dwell — primary bias indicator |
| `err_max_abs` | array of numbers | Largest absolute error during the dwell — worst-case indicator |
| `err_p95` | array of numbers | 95th percentile of `|error|` over the dwell |
| `err_end` | array of numbers | Mean error in the last 20% of the dwell — intra-dwell drift indicator |
| `n_points` | array of integers | Number of finite time steps in the dwell |
| `pitch_bin` | array of integers | Which `plist` bin this dwell falls in |

### `analytics` — high-level performance metrics

Pre-computed analytics derived from `dwell_table`. Designed to give a complete picture of model accuracy trends without requiring further computation, particularly for understanding behaviour near the planning limit.

**`near_limit_threshold`** (number): The observed-temperature threshold used to define "near-limit" dwells throughout this section. For `limit_type = "max"` models it is the top third of `telem_bounds` (dwells where `obs_mean_temp ≥ threshold` are near-limit). For `limit_type = "min"` models it is the bottom third (dwells where `obs_mean_temp ≤ threshold`). Near-limit dwells are where model errors matter most for mission planning.

**`monthly`**: Monthly weighted-mean bias for all dwells and near-limit dwells separately. Weights are `n_points` so longer dwells count more.

```json
"monthly": {
  "all":        {"months": ["2025-01", ...], "mean": [...], "std": [...], "n": [...]},
  "near_limit": {"months": ["2025-01", ...], "mean": [...], "std": [...], "n": [...]}
}
```

**`error_by_temperature`**: Mean bias and 95th-percentile `|error|` binned by `obs_mean_temp` across 20 equal bins spanning `telem_bounds`. Reveals whether errors are temperature-dependent — the most direct indicator of systematic model deficiencies near the limit.

| Field | Description |
|---|---|
| `bin_edges` | Array of 21 temperature values bounding the 20 bins |
| `bin_centers` | Array of 20 bin midpoint temperatures |
| `mean` | Mean `err_mean` per bin; `null` for empty bins |
| `std` | Std of `err_mean` per bin |
| `p95_abs` | 95th percentile of `|err_mean|` per bin — worst-case spread |
| `n` | Number of dwells per bin |

**`period_comparison`**: Compares early (first two-thirds) vs recent (last third) of the evaluation period for all dwells and near-limit dwells. Detects model drift over time.

| Field | Description |
|---|---|
| `split_date` | Chandra date string of the early/recent boundary |
| `all.early` / `all.recent` | `{n, mean, std, p95_abs}` for all dwells in each period |
| `near_limit.early` / `near_limit.recent` | Same, restricted to near-limit dwells |

### Solarheat parameters

| Key | Type | Description |
|---|---|---|
| `solar_params` | object | Parameter group name → array of `[pitch_string, value]` pairs. Covers both direct (`P`) and differential (`dP`) solarheat heating parameters from the model spec. |
| `p_names` | array of strings | Names of the direct solarheat parameter groups (e.g. `"cea0__P_hrcs"`). |
| `dp_names` | array of strings | Names of the differential solarheat parameter groups (e.g. `"cea0__dP"`). |

## Running all models — `run_all_models`

`run_all_models` evaluates every model in the `MODELS` registry and writes one `.json.gz` file per model to an output directory. It is designed to be called by a cron job.

```python
from chandra_model_eval import run_all_models

report = run_all_models(
    tstart='2025:001',
    tstop='2025:090',
    outdir='/data/model_eval/results',
    models_root='/proj/sot/ska/data/chandra_models',
    limit_overrides={'pm2thv1t': 227.5},
)
# report == {'succeeded': ['aacccdpt', '1deamzt', ...], 'failed': {}}
```

| Argument | Type | Description |
|---|---|---|
| `tstart` | str | Start time (any xija-accepted format, e.g. `'2025:001'`) |
| `tstop` | str | Stop time |
| `outdir` | str | Output directory; created if it does not exist |
| `models_root` | str | Root of the `chandra_models` repo checkout |
| `limit_overrides` | dict or None | MSID → explicit limit; required for `pm2thv1t`, optional for any other model |
| `models` | list or None | Subset of MSIDs to evaluate; defaults to all 14 |

Output files are named `{msid}.json.gz` and written to `outdir`. Each run overwrites the previous file.

The function never raises — failures per model are caught, logged at ERROR level, and collected in the return value. The return dict has two keys:

- `'succeeded'` — list of MSIDs that completed successfully
- `'failed'` — dict of `{msid: error_message}` for any that raised an exception

All progress and errors are logged to the `chandra_model_eval.models` logger. Configure a handler on the root logger to capture output:

```python
import logging
logging.basicConfig(
    level=logging.INFO,
    filename='/data/model_eval/run.log',
)
```

## Command-line interface

The package installs a `chandra-model-eval` command that wraps `run_all_models`. It is the recommended way to invoke the pipeline from a cron job or shell script.

```
chandra-model-eval [tstart tstop] outdir models_root [options]
```

**Time range** — provide explicit start and stop times, or use `--trailing-days` for a rolling window ending at the current UTC time:

```bash
# Explicit window
chandra-model-eval 2025:001 2026:001 /data/model_eval ~/AXAFLIB/chandra_models

# Rolling 365-day window ending at UTC now (typical cron usage)
chandra-model-eval --trailing-days 365 /data/model_eval ~/AXAFLIB/chandra_models \
    --limit-override pm2thv1t=227.5 \
    --log-file /data/model_eval/run.log

# Rolling window with an explicit end date (useful when data lags current time)
chandra-model-eval --trailing-days 365 --end-date 2025:180 \
    /data/model_eval ~/AXAFLIB/chandra_models \
    --limit-override pm2thv1t=227.5
```

**Options:**

| Flag | Description |
|---|---|
| `--trailing-days N` | Rolling window of N days; window ends at UTC now (or `--end-date` if given) |
| `--end-date DATE` | End of the `--trailing-days` window (e.g. `2025:180`); defaults to UTC now |
| `--limit-override MSID=VALUE` | Override the planning limit for one model; repeatable; required for `pm2thv1t` |
| `--models MSID ...` | Run only the listed models (default: all 14) |
| `--log-level LEVEL` | `DEBUG`, `INFO` (default), `WARNING`, or `ERROR` |
| `--log-file PATH` | Write log output to a file instead of stderr |

**Exit codes:** 0 if all models succeeded; 1 if any model failed. Failed MSIDs and their error messages are printed to stderr.

```bash
# Run a quick two-model test
chandra-model-eval --trailing-days 30 /tmp/eval_test ~/AXAFLIB/chandra_models \
    --models 1dpamzt aacccdpt --log-level DEBUG
```

## Using the MODELS registry

The `MODELS` dict maps MSID name to class, useful for iterating over all models or instantiating dynamically:

```python
from chandra_model_eval import MODELS, MODEL_SPECS

MODELS_ROOT = '/proj/sot/ska/data/chandra_models'

for msid, cls in MODELS.items():
    spec = f'{MODELS_ROOT}/{MODEL_SPECS[msid]}'
    limit = 210.0 if msid == 'pm2thv1t' else None   # pm2thv1t has no spec default
    m = cls(model_spec=spec, limit=limit)
    result = m.evaluate('2025:001', '2025:090')
```

## Adding a new model

1. Subclass `ChandraModel`.
2. In `__init__`, set `self.msid`, `self.limit_type`, and `self.model_spec` **first** (required by `_read_spec_limits()`), then call `_read_spec_limits()` to populate `self.all_limits` and `self.units`, resolve `limit`, and build `self.model_init`.
3. `model_init` must include the primary MSID node (used as starting temperature in `run()`) and any pseudo-nodes or fixed-value components the model requires.
4. Add the new class to the `MODELS` dict and `MODEL_SPECS` dict.

```python
class ModelXXXXX(ChandraModel):
    def __init__(self, model_spec, limit=None):
        self.msid = 'xxxxx'
        self.limit_type = 'max'   # or 'min'
        self.model_spec = model_spec
        self.all_limits, self.units = self._read_spec_limits()
        if limit is None:
            limit = self._default_limit()
        self.limit = limit
        self.model_init = {'xxxxx': limit, 'xxx0': limit}
```

## `model_init` reference

Each model's `model_init` dict, showing which components are initialized and to what values:

| MSID | model_init keys | Notes |
|---|---|---|
| `aacccdpt` | `aacccdpt`, `aca0` | both nodes set to limit |
| `1deamzt` | `1deamzt`, `dea0`, `dpa_power` | power initialized to 0 |
| `1dpamzt` | `1dpamzt`, `dpa0`, `dpa_power` | power initialized to 0 |
| `fptemp` | `fptemp`, `1cbat`, `sim_px` | `1cbat=-55.0`, `sim_px=110.0` (ACIS-S defaults) |
| `1pdeaat` | `1pdeaat`, `pin1at`, `dpa_power` | power initialized to 0 |
| `pftank2t` | `pftank2t`, `pf0tank2t` | both nodes set to limit |
| `4rt700t` | `4rt700t`, `oba0` | both nodes set to limit |
| `tpc_fsse` | `tpc_fsse`, `fsse0` | both nodes set to limit |
| `tpcm_rw5` | `tpcm_rw5`, `rw50` | both nodes set to limit |
| `pline03t` | `pline03t`, `pline03t0` | both nodes set to limit (cold limit) |
| `pline04t` | `pline04t`, `pline04t0` | both nodes set to limit (cold limit) |
| `pm1thv2t` | `pm1thv2t`, `mups0` | both nodes set to limit |
| `pm2thv1t` | `pm2thv1t`, `mups0`, `mups1` | all nodes set to limit |
| `2ceahvpt` | `2ceahvpt`, `cea0`, `cea1`, `eclipse`, `dpa_power` | eclipse=False, power=0 |
