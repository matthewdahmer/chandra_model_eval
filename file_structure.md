# Output File Structure — `{msid}.json.gz`

```
{msid}.json.gz
│
├── msid              str    MSID name (e.g. "1dpamzt")
├── limit             float  Planning warning limit value
├── limit_type        str    "max" or "min"
├── all_limits        dict   All spec limit entries keyed by name (e.g. "planning.warning.high")
├── units             str    Temperature unit ("degC" or "degF")
│
├── spec_md5          str    MD5 of the model spec JSON on disk
├── spec_github_url   str|null  Permanent GitHub blob URL (commit SHA)
├── spec_github_release str|null  Git tag or describe string
│
├── datestart         str    First time step as Chandra date (YYYY:DDD:HH:MM:SS.sss)
├── datestop          str    Last time step
├── times             [float]  CXC seconds, one per model time step (~328 s cadence)
├── predicted         [float|null]  Model temperature at each time step
├── observed          [float|null]  Telemetry temperature at each time step
├── residuals         [float|null]  observed − predicted at each time step
│
├── stats             dict   Global residual statistics over the full window
│   ├── n             int    Number of finite residual samples
│   ├── mean          float  Mean residual (bias)
│   ├── std           float  Standard deviation
│   ├── rms           float  Root mean square
│   ├── max_abs       float  Max |residual|
│   └── p05/p25/p50/p75/p95  float  Percentiles
│
├── violations        dict   Times where predicted exceeds the limit
│   ├── count         int    Number of violation samples
│   ├── fraction      float  Fraction of all samples in violation
│   ├── times         [float]  CXC seconds of each violation
│   └── values        [float]  Predicted temperature at each violation
│
├── pitch_analysis
│   ├── plist         [float]  Pitch bin boundary values (degrees)
│   ├── telem_bounds  [float, float]  [global_min, global_max] of observed temp
│   │
│   ├── metadata      dict   Per-bin dwell metadata; keyed by bin index (str)
│   │   └── "N"       [row, ...]  Each row is a kadi state record with fields:
│   │                   tstart, tstop, pitch, simpos, pcad_mode, ccd_count, etc.
│   │
│   ├── telem_segments  dict  Per-bin observed-temperature traces; keyed by bin index (str)
│   │   └── "N"       [[seg], ...]  Each seg is [[rel_seconds, temp], ...] from dwell start
│   │
│   ├── err_segments  dict  Same structure as telem_segments but values are residuals
│   │   └── "N"       [[seg], ...]
│   │
│   ├── segment_norm  dict  Per-bin starting-temperature normalisation [0–1]; keyed by bin index (str)
│   │   └── "N"       [float, ...]  One value per dwell; 0=global_min, 1=global_max
│   │
│   └── pitch_bin_statistics  dict  Per-bin summary; keyed by bin index (str)
│       └── "N"
│           ├── n_segments    int    Number of dwells in this bin
│           ├── n_points      int    Total finite time steps across all dwells
│           ├── telem         dict   Observed-temperature statistics (pooled across dwells)
│           │   ├── mean/std/rms/max_abs  float
│           │   ├── p05/p25/p50/p75/p95  float
│           │   ├── segment_mean_mean  float  Mean of per-dwell means (dwell-to-dwell level)
│           │   ├── segment_mean_std   float  Std of per-dwell means
│           │   ├── segment_drift_mean float  Mean intra-dwell linear slope (units/ks)
│           │   └── segment_drift_std  float  Std of intra-dwell slopes
│           └── error         dict   Same structure as telem but for residuals
│
├── dwell_table       dict   Parallel lists, one entry per qualifying dwell (>1 hr, NPNT)
│   ├── tstart        [float]        Dwell start in CXC seconds (chronological)
│   ├── datestart     [str]          Dwell start as Chandra date string
│   ├── pitch         [float]        Mean pitch during dwell (degrees)
│   ├── simpos        [int]          SIM-Z position
│   ├── fep_count     [int]          Number of FEPs active (from kadi state)
│   ├── ccd_count     [int]          Number of CCDs active (from kadi state)
│   ├── clocking      [int]          ACIS clocking flag 0/1 (from kadi state)
│   ├── off_nom_roll  [float]        Off-nominal roll angle (degrees, from kadi state)
│   ├── dist_satearth [float|null]  Earth–spacecraft distance (km) at dwell start (cheta)
│   ├── obs_start_temp [float]       Observed temperature at dwell start
│   ├── obs_max_temp  [float]        Peak observed temperature during dwell
│   ├── obs_mean_temp [float]        Mean observed temperature during dwell
│   ├── err_mean      [float]        Mean residual over dwell (bias)
│   ├── err_max_abs   [float]        Max |residual| over dwell
│   ├── err_p95       [float]        95th percentile of |residual| over dwell
│   ├── err_end       [float]        Mean residual in the last 20% of dwell (drift indicator)
│   ├── n_points      [int]          Finite time steps in dwell
│   ├── pitch_bin     [int]          Pitch bin index
│   │
│   │   (2ceahvpt only — median raw_vals over dwell duration from cheta)
│   ├── 2imonst       [float|null]   HRC-I monitor on/off (raw: 0=OFF, 1=ON)
│   ├── 2sponst       [float|null]   HRC-S outer shield on/off (raw: 0=OFF, 1=ON)
│   ├── 2s2onst       [float|null]   HRC-S inner shield on/off (raw: 0=OFF, 1=ON)
│   ├── 224pcast      [float|null]   +24V power supply on/off (raw inverted: 0=ON, 1=OFF)
│   ├── 215pcast      [float|null]   +15V power supply on/off (raw inverted: 0=ON, 1=OFF)
│   └── aoeclips      [float|null]   Earth limb clip on/off (raw: 0=no clip, 1=clipping)
│
├── analytics         dict   Pre-computed diagnostics
│   ├── near_limit_threshold  float  Temperature above/below which a dwell is "near-limit"
│   │                                (top third of observed range for max-limit models;
│   │                                 bottom third for min-limit models)
│   ├── monthly       dict   Monthly weighted-mean bias trend
│   │   ├── all         {months, mean, std, n}  All dwells
│   │   └── near_limit  {months, mean, std, n}  Near-limit dwells only
│   │       └── months  ["YYYY-MM", ...]
│   │           mean    [float]  Weighted mean error per month
│   │           std     [float]  Weighted std per month
│   │           n       [int]    Number of dwells per month
│   ├── error_by_temperature  dict  Mean error in 20 equal temperature bins
│   │   ├── bin_edges   [float]  21 edges spanning telem_bounds
│   │   ├── bin_centers [float]  20 bin midpoints
│   │   ├── mean        [float|null]  Mean error per bin (null if no dwells)
│   │   ├── std         [float|null]  Std per bin
│   │   ├── p95_abs     [float|null]  95th percentile of |error| per bin
│   │   └── n           [int]    Dwell count per bin
│   └── period_comparison  dict  Early 2/3 vs recent 1/3 of the evaluation window
│       ├── split_date  str    Chandra date at the 2/3 boundary
│       ├── all         {early, recent}  Stats over all dwells
│       └── near_limit  {early, recent}  Stats over near-limit dwells only
│           └── early/recent: {n, mean, std, p95_abs}
│
├── solar_heat_components  [object, ...]  One entry per solarheat component (excludes SolarHeatOffNomRoll)
│   ├── name        str    Component string (e.g. "solarheat__cea0", "all_simz_solarheat__1pdeaat")
│   ├── node        str    Pseudo-node being heated
│   ├── class       str    xija component class name
│   ├── epoch       str    Reference date; t_days = (t - epoch) / 86400
│   ├── tau         float  Time constant (days); dP/tau is the heating rate slope
│   ├── ampl        float  Amplitude of annual sinusoidal heating variation
│   ├── bias        float  Constant offset added to all P values (SolarHeat variants only)
│   ├── dh_heater   float  Detector housing heater power offset (SimZDep variants only)
│   ├── hrc_bias    float  (SolarHeatHrc only)
│   ├── hrci_bias   float  (SolarHeatHrcOpts/HrcMult only)
│   ├── hrcs_bias   float  (SolarHeatHrcOpts/HrcMult only)
│   ├── P_pitches   [float]  Pitch grid for P values (degrees)
│   ├── P           [float] or dict  Baseline solar heating per pitch
│   │                For SolarHeat variants: flat list aligned with P_pitches
│   │                For SimZDep variants: {"hrcs": [...], "hrci": [...], ...}
│   ├── dP_pitches  [float]  Pitch grid for dP values (may differ from P_pitches)
│   └── dP          [float]  Degradation heating increment aligned with dP_pitches
│
└── dpa_power       dict   DPA power lookup table; empty dict if model has no dpa_power parameters
    ├── lookup      dict   Pattern string → power value (watts)
    │               Keys encode instrument state: position order is fep_count, ccd_count,
    │               vid_board, clocking; 'x' means wildcard (e.g. "1xx0" = fep=1, clk=0,
    │               any ccd/vid_board). The most specific matching entry is used at run time.
    ├── mult        float  Scale factor applied to the looked-up power value
    └── bias        float  Constant offset added after scaling
```

---

## Field Explanations

### Model identity and limits

**`msid`** — The engineering telemetry MSID being predicted (e.g. `"1dpamzt"`). Used as the file stem and as the primary key in all xija model lookups.

**`limit`** — The active planning warning limit in the model's native units. This is the threshold used to define violations and to characterise "near-limit" behaviour throughout the analytics. For most models this is read directly from the spec's `planning.warning.high` (or `.low`) entry; it can be overridden at run time.

**`limit_type`** — Either `"max"` (temperature must stay below `limit`) or `"min"` (temperature must stay above `limit`). Controls the sign convention for violations, near-limit classification, and the direction of `error_by_temperature` interpretation.

**`all_limits`** — Every limit entry present in the model spec for this MSID, keyed by limit name (e.g. `"planning.warning.high"`, `"odb.warning.high"`). Useful for context when `limit` has been overridden or when ACIS-configuration-specific data-quality limits are relevant (notably `fptemp`).

**`units`** — Temperature unit from the spec, either `"degC"` or `"degF"`. All temperature values in the file (limits, time-series, analytics thresholds, bin edges) are in these units.

---

### Spec provenance

**`spec_md5`** — MD5 hex digest of the model spec JSON file read from disk at run time. Use this to verify that the spec used for a given run matches a known version, or to detect if the file has been modified.

**`spec_github_url`** — Permanent GitHub blob URL constructed from the commit SHA of the `chandra_models` repo at run time (e.g. `https://github.com/sot/chandra_models/blob/{sha}/chandra_models/xija/dpa/dpa_spec.json`). Stays valid indefinitely as long as the commit exists. `null` if git was unavailable or the spec is not inside a git repository.

**`spec_github_release`** — The git tag name if the repo was exactly on a release tag at run time, otherwise the output of `git describe --tags` (e.g. `"3.73.1-4-gabcdef"`). `null` if no tags are reachable. Together with `spec_md5` this uniquely identifies which version of the model was used.

---

### Time-series data

All arrays have the same length and the same time axis. The model is run internally with a 7-day warm-up before `tstart` to eliminate initial-condition transients; that data is discarded and does not appear here.

**`datestart` / `datestop`** — Human-readable Chandra date strings (`YYYY:DDD:HH:MM:SS.sss`) for the first and last time steps. Provided for quick inspection; the numeric `times` array is the authoritative time axis.

**`times`** — CXC seconds (seconds since 1998-01-01 00:00:00 UTC) for every model time step. The cadence is approximately 328 seconds, set by the xija integrator. Convert with `CxoTime(times).date` or `.unix`.

**`predicted`** — Model-predicted temperature at each time step (`mvals` from xija). This is what the thermal model says the temperature should be given the commanded attitude and instrument configuration.

**`observed`** — Actual telemetry temperature at each time step (`dvals` from the engineering archive). This is the ground truth against which the model is evaluated. `null` where the telemetry fetch returned NaN (e.g. gaps in the archive).

**`residuals`** — `observed − predicted` at each time step. Positive values mean the spacecraft ran hotter than the model predicted; negative values mean it ran cooler. `null` wherever either `predicted` or `observed` is null.

---

### `stats`

Global residual statistics computed over the entire evaluation window, with NaN values excluded. These give an overall picture of model accuracy but can mask temperature-dependent or time-varying bias — use `analytics` for those.

- **`n`** — Number of finite residual samples. Divide by the expected sample count to gauge data coverage.
- **`mean`** — Mean bias. A non-zero mean indicates a systematic offset between the model and telemetry over the full period.
- **`std`** — Standard deviation of residuals, measuring scatter around the mean bias.
- **`rms`** — Root mean square residual, combining bias and scatter into a single magnitude.
- **`max_abs`** — Largest single-point absolute residual. Can be driven by brief anomalies or telemetry glitches.
- **`p05`–`p95`** — Percentiles of the residual distribution. The spread between `p05` and `p95` characterises the typical operating range of error; the asymmetry between `p50` and zero confirms or contradicts `mean`.

---

### `violations`

**`count`** / **`fraction`** — Number and proportion of time steps where `predicted` crosses `limit` (above limit for `"max"`, below for `"min"`). Because the time series is at ~328 s cadence, `count × 328 / 86400` gives approximate violation hours.

**`times`** / **`values`** — CXC seconds and predicted temperatures at each violating time step. Useful for locating which observations or attitudes drove the violation. Note that violations in `predicted` do not necessarily mean the spacecraft was actually near its limit during those times — they reflect what the model computed, which may itself be in error.

---

### `pitch_analysis`

All pitch-binned data is derived by splitting the evaluation period into NPNT dwells by pitch angle. Dwells shorter than one hour are excluded. Bin indices throughout are zero-based integers into `plist`, stored as strings when used as JSON keys.

**`plist`** — Pitch bin boundary values in degrees, derived from the solarheat pitch grid in the model spec. There are N+1 boundaries for N bins. A dwell with pitch p falls in bin i where `plist[i] ≤ p < plist[i+1]`.

**`telem_bounds`** — `[global_min, global_max]` of observed temperature across the entire evaluation window. Used as the normalisation range for `segment_norm` and as the domain for `error_by_temperature` bins in `analytics`.

**`metadata`** — Per-bin array of kadi state records, one per dwell. Each record carries the full NPNT state: `tstart`, `tstop`, `pitch`, `off_nom_roll`, `simpos`, `ccd_count`, `fep_count`, `clocking`, `vid_board`, `pcad_mode`, `trans_keys`. The index into this array matches the index into `telem_segments` and `err_segments` for the same bin.

**`telem_segments`** — Per-bin list of observed-temperature traces. Each trace is a list of `[relative_seconds, temperature]` pairs starting from zero at dwell start. Relative time rather than absolute CXC seconds is used so traces from different dwells can be overlaid directly for visual comparison.

**`err_segments`** — Same structure as `telem_segments` but values are `observed − predicted` residuals. Positive = model ran cold (underpredicted); negative = model ran hot (overpredicted). Aligns index-for-index with `telem_segments`.

**`segment_norm`** — Per-bin list of starting-temperature normalisation values, one float per dwell. The starting temperature of the dwell is expressed as a fraction of `telem_bounds` range (0 = global minimum, 1 = global maximum). Intended for colour-coding traces when plotting so that all pitch bins share a consistent colour scale.

**`pitch_bin_statistics`** — Per-bin summary statistics, keyed by bin index string. Each entry contains:

- **`n_segments`** — Number of qualifying dwells in this bin.
- **`n_points`** — Total finite time steps pooled across all dwells, giving a sense of how much data underlies the statistics.
- **`telem`** — Statistics for observed temperature pooled across all points in all dwells in this bin:
  - `mean/std/rms/max_abs` — standard descriptors of the temperature distribution at this pitch.
  - `p05`–`p95` — percentiles of the observed temperature distribution.
  - `segment_mean_mean` — mean of per-dwell average temperatures. Reflects the typical temperature level a single dwell at this pitch reaches, averaging out within-dwell variation.
  - `segment_mean_std` — std of per-dwell average temperatures. Large values mean some dwells at this pitch are consistently warmer or cooler than others, suggesting the model is sensitive to conditions beyond pitch alone (e.g. roll, instrument configuration).
  - `segment_drift_mean` — mean intra-dwell linear slope in units/ks, fit to each dwell individually and then averaged. A positive value means the spacecraft tends to warm during dwells at this pitch.
  - `segment_drift_std` — std of intra-dwell slopes across dwells.
- **`error`** — Same structure as `telem` but for residuals (`observed − predicted`). The diagnostically important fields are:
  - `mean` — systematic bias at this pitch. If this is large and consistent across bins, the overall model offset is the issue. If it varies strongly with pitch bin, the solarheat `P` parameters are wrong.
  - `segment_drift_mean` — mean intra-dwell drift of the error in units/ks. A non-zero value means the model consistently runs ahead of or behind telemetry as the dwell progresses, pointing to incorrect solarheat or thermal-mass parameters.

---

### `dwell_table`

A compact row-per-dwell table stored as parallel arrays, sorted chronologically. Contains one entry for every qualifying dwell (NPNT, >1 hr) regardless of pitch bin, making it the primary input for time-trend and temperature-dependent analytics.

- **`tstart` / `datestart`** — Dwell start time as CXC seconds and as a Chandra date string. Use `tstart` for arithmetic; `datestart` for display.
- **`pitch`** — Mean pitch angle during the dwell in degrees. This is the kadi state pitch, which is constant within a single NPNT interval by construction.
- **`simpos`** — SIM-Z position. Distinguishes ACIS-I, ACIS-S, and HRC pointings, which affects focal-plane and nearby-component temperatures.
- **`fep_count`** — Number of ACIS FEPs active during the dwell, from kadi commanded state. Drives DPA power dissipation; directly relevant for `1dpamzt`, `1deamzt`, and `1pdeaat` models.
- **`ccd_count`** — Number of ACIS CCDs active during the dwell, from kadi commanded state.
- **`clocking`** — ACIS clocking state (0 = idle, 1 = clocking) from kadi. A clocking ACIS draws more power, affecting DPA-heated nodes.
- **`off_nom_roll`** — Off-nominal roll angle in degrees at dwell start, from kadi. Non-zero roll changes solar illumination geometry and can affect heating for models with roll-dependent solarheat components.
- **`dist_satearth`** — Earth–spacecraft distance in km at the start of the dwell, fetched from the cheta engineering archive (`dist_satearth` MSID). `null` if the archive fetch failed or returned no data for the time range.
- **`obs_start_temp`** — Observed temperature at the very start of the dwell. Useful for studying how the starting condition affects subsequent model error.
- **`obs_max_temp`** — Peak observed temperature during the dwell. The key quantity for max-limit risk assessment in individual dwells.
- **`obs_mean_temp`** — Mean observed temperature during the dwell. Used throughout `analytics` as the representative temperature for this dwell (e.g. for `error_by_temperature` binning and `near_limit_threshold` classification).
- **`err_mean`** — Mean of `observed − predicted` over the full dwell. The primary per-dwell bias indicator; positive means the model underpredicted (ran cold).
- **`err_max_abs`** — Largest absolute residual within the dwell. Captures the worst-case single-point error, which may be driven by a transient the model doesn't capture.
- **`err_p95`** — 95th percentile of `|residual|` within the dwell. More robust than `err_max_abs` for characterising typical worst-case error while ignoring outlier spikes.
- **`err_end`** — Mean residual in the last 20% of the dwell. If this differs from `err_mean`, the model is drifting during the dwell — accumulating error as the thermal state evolves. A consistently positive `err_end` with near-zero `err_mean` means the model starts accurate but falls behind late in long dwells.
- **`n_points`** — Number of finite time steps in the dwell. Proportional to dwell duration (~328 s cadence). Used as the weight in `analytics.monthly`.
- **`pitch_bin`** — Integer index into `plist` for this dwell's pitch bin. Links `dwell_table` rows back to the corresponding `pitch_analysis` entries.

**HRC-specific columns** (`2ceahvpt` model only) — values are derived from `.raw_vals` (integer encoding) in cheta, then the median is taken over the dwell interval. `null` if the archive fetch failed or the dwell has no valid samples.

- **`2imonst`** — HRC-I monitor status. `raw_vals` encoding: 0 = OFF, 1 = ON.
- **`2sponst`** — HRC-S outer anti-coincidence shield status. Same 0/1 encoding.
- **`2s2onst`** — HRC-S inner anti-coincidence shield status. Same 0/1 encoding.
- **`224pcast`** — HRC +24 V power converter status. `raw_vals` encoding is inverted before storage: `1 - raw_vals`, so 0 = ON, 1 = OFF.
- **`215pcast`** — HRC +15 V power converter status. Same inverted encoding as `224pcast`.
- **`aoeclips`** — Earth limb clip status. `raw_vals` encoding: 0 = no clip (NECL), 1 = clipping (ECL).

---

### `analytics`

Pre-computed diagnostics derived entirely from `dwell_table`. Designed to give a complete picture of model accuracy without requiring further computation. All `mean` values are means of `err_mean` (per-dwell bias), not of the raw time-series residuals.

**`near_limit_threshold`** — A single temperature value that divides dwells into "routine" vs "near-limit." Computed from the observed temperature range over the evaluation window, not from the limit value itself:

- Max-limit models: `t_min + (t_max − t_min) × 2/3` — the top third of the observed range
- Min-limit models: `t_min + (t_max − t_min) × 1/3` — the bottom third

Every `near_limit` sub-statistic below uses `obs_mean_temp ≥ threshold` (max) or `≤ threshold` (min) to select dwells. Because the threshold is derived from the *observed* operating range rather than the limit value, if the spacecraft consistently runs well within its margin the "near-limit" dwells may not actually be close to the limit — they are the warmest (or coldest) dwells that were observed during the evaluation period.

**`monthly`** — Monthly weighted-mean bias computed separately for all dwells and near-limit dwells. Weights are `n_points`, so longer dwells count more than short ones. Each of `all` and `near_limit` contains parallel arrays `months` (`"YYYY-MM"` strings), `mean`, `std`, and `n` (number of dwells in the month).

What to look for: a trend in `all.mean` indicates the model is drifting over time and the spec needs updating. A trend that appears in `near_limit.mean` but not `all.mean` means the drift is temperature-dependent and concentrated near the limit — the more operationally significant case.

**`error_by_temperature`** — Dwells are sorted into 20 equal temperature bins spanning `telem_bounds` by their `obs_mean_temp`, and bias statistics are computed per bin. Fields: `bin_edges` (21 boundaries), `bin_centers` (20 midpoints), `mean`, `std`, `p95_abs`, and `n` per bin. Empty bins are `null`.

What to look for: a flat `mean` across bins means the model has a consistent offset regardless of temperature. A `mean` that rises or falls with temperature means the heating or cooling coefficients are wrong at some temperatures. The operationally critical question is whether `mean` is systematically large specifically in the high-temperature bins (max-limit models) or low-temperature bins (min-limit models), since those correspond to dwells near the planning limit.

**`period_comparison`** — The evaluation window is split at the two-thirds dwell-count boundary into an "early" and "recent" period, and error statistics are computed for each, for all dwells and near-limit dwells separately.

- **`split_date`** — Chandra date of the early/recent boundary.
- **`all.early` / `all.recent`** and **`near_limit.early` / `near_limit.recent`** — each contains `{n, mean, std, p95_abs}`. `mean` is the mean of `err_mean` across dwells in that period; `p95_abs` is the 95th percentile of `|err_mean|`, reflecting worst-case single-dwell error rather than average behaviour.

What to look for: if `recent.mean` is larger in magnitude than `early.mean`, the model is degrading. If that difference is larger in `near_limit` than in `all`, the degradation is concentrated at high temperatures — the highest-priority trigger for a model update. A change in `p95_abs` without a change in `mean` means the model is becoming less consistent (larger scatter) without a systematic shift in bias.

---

### Solarheat parameters

**`solar_heat_components`** — List of solarheat component dicts, one per `SolarHeat` component in the model. `SolarHeatOffNomRoll` is excluded (different physics, not pitch-table based). Each dict contains everything needed to reproduce the heating calculation at any pitch and time:

```
heat(pitch, t) = P(pitch) + dP(pitch) * (t_days / tau) + ampl * cos(t_phase) + bias
```

where `t_days = (t - epoch) / 86400` and `dP(pitch) / tau` is the heating rate slope in units/day. P and dP are linearly interpolated onto the requested pitch from their respective pitch grids. Degradation is linear; the exponential var_func option in xija is no longer used.

**Fields present in every component:**

- **`name`** — xija component string (e.g. `"solarheat__cea0"`, `"all_simz_solarheat__1pdeaat"`). The prefix before `solarheat__` identifies the subclass: no prefix → `SolarHeat`; `all_simz_` → `AllSimZSolarHeat` (4 instruments: HRC-S, HRC-I, ACIS-S, ACIS-I); `psmc_` → `AcisPsmcSolarHeat`; `hrc_is_acis_simz_` → `HrcISAcisSimZSolarHeat`; `hrc_acis_is_simz_` → `AcisISHrcSimZSolarHeat`.
- **`node`** — pseudo-node being heated.
- **`class`** — xija Python class name.
- **`epoch`** — reference date string; `t_days` is measured from this date.
- **`tau`** — time constant in days. `dP(pitch) / tau` is the heating rate slope.
- **`ampl`** — amplitude of the annual sinusoidal variation added to all heating values.
- **`P_pitches`** — pitch grid for P values (degrees).
- **`P`** — baseline solar heating. For `SolarHeat` variants: a flat list aligned with `P_pitches`. For `SimZDepSolarHeat` variants: a dict keyed by instrument name (e.g. `{"hrcs": [...], "hrci": [...], "aciss": [...], "acisi": [...]}`), one list per instrument aligned with `P_pitches`.
- **`dP_pitches`** — pitch grid for dP values. May differ from `P_pitches`; xija interpolates between the two grids at run time.
- **`dP`** — degradation heating increment aligned with `dP_pitches`. One shared curve per component regardless of how many P curves exist. Always present; may be all zeros.

**Conditionally present:**

- **`bias`** — constant offset added to all P values before interpolation (`SolarHeat` variants only).
- **`dh_heater`** — additional heating applied when the detector housing heater is active (`SimZDepSolarHeat` variants only).
- **`hrc_bias`** — heating offset applied when SIM-Z is at HRC (`SolarHeatHrc` only).
- **`hrci_bias`** / **`hrcs_bias`** — per-instrument heating offsets for HRC-I and HRC-S (`SolarHeatHrcOpts` and `SolarHeatHrcMult` only).

---

### DPA power parameters

**`dpa_power`** — DPA power lookup table extracted from the xija model. Present in models that predict DPA-heated nodes (`1dpamzt`, `1deamzt`, `1pdeaat`); empty dict `{}` for all other models.

The computed power at any moment is `lookup[best_match] * mult + bias`, where `best_match` is the most specific entry in `lookup` that matches the current instrument state.

**`lookup`** — Dict mapping pattern strings to power values in watts. Each key is a 4-character string encoding the instrument state in order: `fep_count`, `ccd_count`, `vid_board`, `clocking`. The character `x` is a wildcard meaning "match any value for this dimension." For example:

| Key | Meaning |
|---|---|
| `"0xxx"` | 0 FEPs active, all other dimensions irrelevant |
| `"1xx0"` | 1 FEP active, clocking off, ccd_count/vid_board irrelevant |
| `"66x0"` | 6 FEPs, 6 CCDs, clocking off, vid_board irrelevant |
| `"6611"` | 6 FEPs, 6 CCDs, vid_board=1, clocking on |

At run time, xija selects the most specific matching entry (fewest wildcards) for the current state. The set of keys varies by model — some models parameterise by fep_count and clocking only, others include ccd_count.

**`mult`** — Multiplicative scale factor applied to the looked-up power value.

**`bias`** — Constant offset (watts) added after scaling.
