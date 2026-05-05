"""Xija thermal model wrappers for Chandra mission planning and evaluation."""

import gzip
import hashlib
import json
import logging
import os
import subprocess
from dataclasses import dataclass, field
from typing import Optional

from .calc_model_data import (
    bin_data_by_pitch,
    build_dwell_table,
    compute_analytics,
    compute_pitch_bin_statistics,
    get_dpa_power_params,
    get_npnt_state_data,
    get_pitch_midpoints,
    get_solarheat_components,
)

import numpy as np
import xija

logger = logging.getLogger(__name__)


MODEL_SPECS = {
    'aacccdpt': 'chandra_models/xija/aca/aca_spec.json',
    '1deamzt':  'chandra_models/xija/dea/dea_spec.json',
    '1dpamzt':  'chandra_models/xija/dpa/dpa_spec.json',
    'fptemp':   'chandra_models/xija/acisfp/acisfp_spec_matlab.json',
    '1pdeaat':  'chandra_models/xija/psmc/psmc_spec.json',
    'pftank2t': 'chandra_models/xija/pftank2t/pftank2t_spec.json',
    '4rt700t':  'chandra_models/xija/fwdblkhd/4rt700t_spec.json',
    'tpc_fsse': 'chandra_models/xija/tpc_fsse/tpc_fsse_spec.json',
    'tpcm_rw5': 'chandra_models/xija/rwa5/tpcm_rw5_spec.json',
    'pline03t': 'chandra_models/xija/pline/pline03t_model_spec.json',
    'pline04t': 'chandra_models/xija/pline/pline04t_model_spec.json',
    'pm1thv2t': 'chandra_models/xija/mups_valve/pm1thv2t_spec.json',
    'pm2thv1t': 'chandra_models/xija/mups_valve/pm2thv1t_spec_matlab.json',
    '2ceahvpt': 'chandra_models/xija/hrc/cea_spec.json',
}


@dataclass
class ModelResult:
    """Results from a model evaluation run."""
    msid: str
    times: np.ndarray
    predicted: np.ndarray
    observed: np.ndarray
    limit: float
    limit_type: str             # 'max' or 'min'
    all_limits: dict            # all limit entries from the spec (excluding 'unit')
    units: str                  # temperature unit from the spec ('degC' or 'degF')
    spec_md5: str               # MD5 hex digest of the model spec file on disk
    spec_github_url: Optional[str]      # permanent GitHub blob URL (uses commit SHA)
    spec_github_release: Optional[str]  # git tag or description; None if not determinable
    # pitch-binned segment traces (populated by evaluate(); empty if computation fails)
    plist: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    telem_segments: dict = field(default_factory=dict)
    err_segments: dict = field(default_factory=dict)
    segment_norm: dict = field(default_factory=dict)
    telem_bounds: tuple = field(default_factory=tuple)
    pitch_bin_statistics: dict = field(default_factory=dict)
    # per-dwell table and high-level analytics
    dwell_table: dict = field(default_factory=dict)
    analytics: dict = field(default_factory=dict)
    # solarheat components from the model spec
    solar_heat_components: list = field(default_factory=list)
    # dpa power lookup table (empty dict if model has no dpa_power parameters)
    dpa_power: dict = field(default_factory=dict)
    # model component inputs: {component_name: array} for every component with dvals
    inputs: dict = field(default_factory=dict)

    @property
    def residuals(self):
        """observed minus predicted"""
        return self.observed - self.predicted

    def stats(self):
        """Residual summary statistics, NaN-excluded."""
        r = self.residuals
        valid = np.isfinite(r)
        r = r[valid]
        if len(r) == 0:
            return {}
        return {
            'n':       int(np.sum(valid)),
            'mean':    float(np.mean(r)),
            'std':     float(np.std(r)),
            'rms':     float(np.sqrt(np.mean(r ** 2))),
            'max_abs': float(np.max(np.abs(r))),
            'p05':     float(np.percentile(r, 5)),
            'p25':     float(np.percentile(r, 25)),
            'p50':     float(np.percentile(r, 50)),
            'p75':     float(np.percentile(r, 75)),
            'p95':     float(np.percentile(r, 95)),
        }

    def violations(self):
        """Times and values where predicted temperature violates the limit."""
        valid = np.isfinite(self.predicted)
        pred = self.predicted[valid]
        times = self.times[valid]
        if self.limit_type == 'max':
            mask = pred > self.limit
        else:
            mask = pred < self.limit
        return {
            'times':    times[mask],
            'values':   pred[mask],
            'count':    int(np.sum(mask)),
            'fraction': float(np.mean(mask)),
        }


class ChandraModel:
    """Base class providing run() and evaluate() for Chandra xija thermal models.

    Subclasses must set msid, limit_type, and model_spec before calling
    _read_spec_limits(), then set limit, units, all_limits, and model_init.

    Attributes set by subclass __init__:
        msid        primary MSID being predicted
        limit_type  'max' or 'min'
        model_spec  path to the xija model spec JSON file
        limit       planning warning limit (in model units)
        units       temperature unit string from the spec ('degC' or 'degF')
        all_limits  dict of all limit entries from the spec (excluding 'unit')
        model_init  component → initial value map for xija set_data calls

    model_init includes the primary MSID node so that run() can set its initial
    condition (planning mode). During evaluate(), only pseudo-nodes are initialized
    and the primary MSID auto-fetches from the telemetry archive.

    Class attributes:
        extra_dwell_msids   list of cheta MSID names whose median value over each
                            dwell should be added to the dwell table. Override in
                            subclasses that need model-specific columns.
    """

    extra_dwell_msids = []

    def __repr__(self):
        return (
            f'{self.__class__.__name__}('
            f'msid={self.msid!r}, limit={self.limit}, units={self.units!r})'
        )

    def _read_spec_limits(self):
        """Read the limits block for self.msid from the model spec JSON.

        Returns (all_limits, units) where all_limits is a dict of every limit
        entry in the spec (excluding the 'unit' key) and units is the 'unit' string.
        """
        with open(self.model_spec) as f:
            spec = json.load(f)
        raw = spec.get('limits', {}).get(self.msid, {})
        units = raw.get('unit', 'degC')
        all_limits = {k: v for k, v in raw.items() if k != 'unit'}
        return all_limits, units

    def _default_limit(self):
        """Return the planning.warning.high/low value from the spec."""
        suffix = 'high' if self.limit_type == 'max' else 'low'
        key = f'planning.warning.{suffix}'
        if key not in self.all_limits:
            raise ValueError(
                f"{self.__class__.__name__}: no '{key}' in spec limits for "
                f"'{self.msid}'; pass limit explicitly."
            )
        return self.all_limits[key]

    def _spec_info(self):
        """Return (md5, github_url, github_release) for the model spec file.

        MD5 is computed from the file on disk. GitHub URL and release are derived
        from the git repository that contains the spec file. Both GitHub fields are
        None if git is unavailable or the file is not inside a git repository.
        """
        with open(self.model_spec, 'rb') as f:
            spec_md5 = hashlib.md5(f.read()).hexdigest()

        spec_github_url = None
        spec_github_release = None

        try:
            spec_dir = os.path.dirname(os.path.abspath(self.model_spec))

            git_root = subprocess.run(
                ['git', 'rev-parse', '--show-toplevel'],
                cwd=spec_dir, capture_output=True, text=True, check=True,
            ).stdout.strip()

            rel_path = os.path.relpath(
                os.path.abspath(self.model_spec), git_root
            ).replace(os.sep, '/')

            commit = subprocess.run(
                ['git', 'rev-parse', 'HEAD'],
                cwd=spec_dir, capture_output=True, text=True, check=True,
            ).stdout.strip()

            spec_github_url = (
                f'https://github.com/sot/chandra_models/blob/{commit}/{rel_path}'
            )

            tag = subprocess.run(
                ['git', 'describe', '--tags', '--exact-match'],
                cwd=spec_dir, capture_output=True, text=True,
            )
            if tag.returncode == 0:
                spec_github_release = tag.stdout.strip()
            else:
                desc = subprocess.run(
                    ['git', 'describe', '--tags'],
                    cwd=spec_dir, capture_output=True, text=True,
                )
                if desc.returncode == 0:
                    spec_github_release = desc.stdout.strip()

        except Exception:
            pass

        return spec_md5, spec_github_url, spec_github_release

    def _build(self, tstart, tstop):
        return xija.ThermalModel(
            self.msid, start=tstart, stop=tstop, model_spec=self.model_spec
        )

    def _compute_pitch_data(self, tstart, tstop, model, times, dvals, mvals):
        """Return pitch-binned data, per-dwell table, and analytics.

        times, dvals, and mvals must already be clipped to the requested tstart/tstop
        (burn-in period excluded). tstart/tstop are still the original requested window,
        used to fetch kadi state data for the same interval.

        Returns (plist, metadata, telem_segments, err_segments, segment_norm, telem_bounds,
                 pitch_bin_statistics, dwell_table, analytics).
        Falls back to empty structures and logs a warning on any failure.
        """
        _empty = ({}, {}, {}, {}, {}, {}, {})
        try:
            from cheta import fetch_eng
            plist = get_pitch_midpoints(model)
            if not plist:
                telem_bounds = (float(np.nanmin(dvals)), float(np.nanmax(dvals)))
                return [], *_empty, telem_bounds
            state_data = get_npnt_state_data(tstart, tstop)
            error = dvals - mvals
            metadata, telem_segments, err_segments, segment_norm, telem_bounds = \
                bin_data_by_pitch(state_data, plist, times, dvals, error)
            pitch_bin_statistics = compute_pitch_bin_statistics(telem_segments, err_segments)

            # Fetch dist_sat_earth for the evaluation window
            dist_sat_earth = None
            try:
                dse = fetch_eng.MSID('dist_satearth', tstart, tstop, filter_bad=True)
                if len(dse.times) > 0:
                    dist_sat_earth = (dse.times, dse.vals)
            except Exception as exc:
                logger.warning('dist_satearth fetch failed for %s: %s', self.msid, exc)

            # Fetch any model-specific extra MSIDs
            extra_msid_data = None
            if self.extra_dwell_msids:
                extra_msid_data = {}
                for msid_name in self.extra_dwell_msids:
                    try:
                        dat = fetch_eng.MSID(msid_name, tstart, tstop, filter_bad=True)
                        if len(dat.times) > 0:
                            vals = dat.raw_vals.copy()
                            if msid_name in ('224pcast', '215pcast'):
                                vals = 1 - vals
                            extra_msid_data[msid_name] = (dat.times, vals)
                    except Exception as exc:
                        logger.warning('extra MSID fetch failed for %s/%s: %s',
                                       self.msid, msid_name, exc)

            dwell_table = build_dwell_table(
                metadata, telem_segments, err_segments, segment_norm, telem_bounds,
                dist_sat_earth=dist_sat_earth,
                extra_msid_data=extra_msid_data,
            )
            analytics = compute_analytics(
                dwell_table, telem_bounds, self.limit, self.limit_type
            )
            return (plist, metadata, telem_segments, err_segments, segment_norm, telem_bounds,
                    pitch_bin_statistics, dwell_table, analytics)
        except Exception as exc:
            logger.warning('pitch data computation failed for %s: %s', self.msid, exc)
            telem_bounds = (float(np.nanmin(dvals)), float(np.nanmax(dvals)))
            return [], *_empty, telem_bounds

    def _compute_solar_params(self, model):
        """Return list of solarheat component dicts. Falls back to [] on failure."""
        try:
            return get_solarheat_components(model)
        except Exception as exc:
            logger.warning('solar param extraction failed for %s: %s', self.msid, exc)
            return []

    def _compute_dpa_power(self, model):
        """Return dpa_power lookup dict. Falls back to {} on failure."""
        try:
            return get_dpa_power_params(model)
        except Exception as exc:
            logger.warning('dpa_power extraction failed for %s: %s', self.msid, exc)
            return {}

    def run(self, tstart, tstop):
        """Run model in planning mode — all model_init values set, including the primary node."""
        model = self._build(tstart, tstop)
        for key, val in self.model_init.items():
            model.comp[key].set_data(val)
        model.make()
        model.calc()
        return model

    def evaluate(self, tstart, tstop):
        """Run model against fetched telemetry; return ModelResult.

        Pseudo-nodes are initialized from model_init. The primary MSID node is
        left unset so xija fetches real telemetry, making dvals the observed
        reference and mvals the model prediction for accuracy assessment.

        The model is run starting 7 days before tstart to allow initial conditions
        to wash out. All output arrays, statistics, and analytics are clipped to
        the requested tstart/tstop window before being returned or written to file.
        """
        from cxotime import CxoTime

        BURN_IN_DAYS = 7
        tstart_cxc = CxoTime(tstart).secs
        tstart_burn = CxoTime(tstart_cxc - BURN_IN_DAYS * 86400.0).date

        model = self._build(tstart_burn, tstop)
        for key, val in self.model_init.items():
            if key != self.msid:
                model.comp[key].set_data(val)
        model.make()
        model.calc()
        comp = model.comp[self.msid]

        # Clip burn-in period — keep only times within the requested window
        clip = model.times >= tstart_cxc
        times  = model.times[clip].copy()
        mvals  = comp.mvals[clip].copy()
        dvals  = comp.dvals[clip].copy()

        # Collect dvals for every component that has a full-length array
        inputs = {}
        n_times = len(model.times)
        for comp_name, c in model.comp.items():
            try:
                dv = c.dvals
                if dv is not None and len(dv) == n_times:
                    inputs[comp_name] = dv[clip].copy()
            except Exception:
                pass

        spec_md5, spec_github_url, spec_github_release = self._spec_info()

        plist, metadata, telem_segments, err_segments, segment_norm, telem_bounds, \
            pitch_bin_statistics, dwell_table, analytics = \
            self._compute_pitch_data(tstart, tstop, model, times, dvals, mvals)
        solar_heat_components = self._compute_solar_params(model)
        dpa_power = self._compute_dpa_power(model)

        return ModelResult(
            msid=self.msid,
            times=times,
            predicted=mvals,
            observed=dvals,
            limit=self.limit,
            limit_type=self.limit_type,
            all_limits=self.all_limits,
            units=self.units,
            spec_md5=spec_md5,
            spec_github_url=spec_github_url,
            spec_github_release=spec_github_release,
            plist=plist,
            metadata=metadata,
            telem_segments=telem_segments,
            err_segments=err_segments,
            segment_norm=segment_norm,
            telem_bounds=telem_bounds,
            pitch_bin_statistics=pitch_bin_statistics,
            dwell_table=dwell_table,
            analytics=analytics,
            solar_heat_components=solar_heat_components,
            dpa_power=dpa_power,
            inputs=inputs,
        )


# ---------------------------------------------------------------------------
# Individual model classes
# ---------------------------------------------------------------------------

class ModelAACCCDPT(ChandraModel):
    def __init__(self, model_spec, limit=None):
        self.msid = 'aacccdpt'
        self.limit_type = 'max'
        self.model_spec = model_spec
        self.all_limits, self.units = self._read_spec_limits()
        if limit is None:
            limit = self._default_limit()
        self.limit = limit
        self.model_init = {'aacccdpt': limit, 'aca0': limit}


class Model1DEAMZT(ChandraModel):
    def __init__(self, model_spec, limit=None):
        self.msid = '1deamzt'
        self.limit_type = 'max'
        self.model_spec = model_spec
        self.all_limits, self.units = self._read_spec_limits()
        if limit is None:
            limit = self._default_limit()
        self.limit = limit
        self.model_init = {'1deamzt': limit, 'dea0': limit, 'dpa_power': 0.0}


class Model1DPAMZT(ChandraModel):
    def __init__(self, model_spec, limit=None):
        self.msid = '1dpamzt'
        self.limit_type = 'max'
        self.model_spec = model_spec
        self.all_limits, self.units = self._read_spec_limits()
        if limit is None:
            limit = self._default_limit()
        self.limit = limit
        self.model_init = {'1dpamzt': limit, 'dpa0': limit, 'dpa_power': 0.0}


class ModelFPTEMP(ChandraModel):
    """ACIS focal plane temperature. 1cbat and sim_px fixed at typical ACIS-S values."""
    def __init__(self, model_spec, limit=None):
        self.msid = 'fptemp'
        self.limit_type = 'max'
        self.model_spec = model_spec
        self.all_limits, self.units = self._read_spec_limits()
        if limit is None:
            limit = self._default_limit()
        self.limit = limit
        self.model_init = {'fptemp': limit, '1cbat': -55.0, 'sim_px': 110.0}


class Model1PDEAAT(ChandraModel):
    def __init__(self, model_spec, limit=None):
        self.msid = '1pdeaat'
        self.limit_type = 'max'
        self.model_spec = model_spec
        self.all_limits, self.units = self._read_spec_limits()
        if limit is None:
            limit = self._default_limit()
        self.limit = limit
        self.model_init = {'1pdeaat': limit, 'pin1at': limit, 'dpa_power': 0.0}


class ModelPFTANK2T(ChandraModel):
    def __init__(self, model_spec, limit=None):
        self.msid = 'pftank2t'
        self.limit_type = 'max'
        self.model_spec = model_spec
        self.all_limits, self.units = self._read_spec_limits()
        if limit is None:
            limit = self._default_limit()
        self.limit = limit
        self.model_init = {'pftank2t': limit, 'pf0tank2t': limit}


class Model4RT700T(ChandraModel):
    def __init__(self, model_spec, limit=None):
        self.msid = '4rt700t'
        self.limit_type = 'max'
        self.model_spec = model_spec
        self.all_limits, self.units = self._read_spec_limits()
        if limit is None:
            limit = self._default_limit()
        self.limit = limit
        self.model_init = {'4rt700t': limit, 'oba0': limit}


class ModelTPC_FSSE(ChandraModel):
    def __init__(self, model_spec, limit=None):
        self.msid = 'tpc_fsse'
        self.limit_type = 'max'
        self.model_spec = model_spec
        self.all_limits, self.units = self._read_spec_limits()
        if limit is None:
            limit = self._default_limit()
        self.limit = limit
        self.model_init = {'tpc_fsse': limit, 'fsse0': limit}


class ModelTPCM_RW5(ChandraModel):
    def __init__(self, model_spec, limit=None):
        self.msid = 'tpcm_rw5'
        self.limit_type = 'max'
        self.model_spec = model_spec
        self.all_limits, self.units = self._read_spec_limits()
        if limit is None:
            limit = self._default_limit()
        self.limit = limit
        self.model_init = {'tpcm_rw5': limit, 'rw50': limit}


class ModelPLINE03T(ChandraModel):
    def __init__(self, model_spec, limit=None):
        self.msid = 'pline03t'
        self.limit_type = 'min'
        self.model_spec = model_spec
        self.all_limits, self.units = self._read_spec_limits()
        if limit is None:
            limit = self._default_limit()
        self.limit = limit
        self.model_init = {'pline03t': limit, 'pline03t0': limit}


class ModelPLINE04T(ChandraModel):
    def __init__(self, model_spec, limit=None):
        self.msid = 'pline04t'
        self.limit_type = 'min'
        self.model_spec = model_spec
        self.all_limits, self.units = self._read_spec_limits()
        if limit is None:
            limit = self._default_limit()
        self.limit = limit
        self.model_init = {'pline04t': limit, 'pline04t0': limit}


class ModelPM1THV2T(ChandraModel):
    def __init__(self, model_spec, limit=None):
        self.msid = 'pm1thv2t'
        self.limit_type = 'max'
        self.model_spec = model_spec
        self.all_limits, self.units = self._read_spec_limits()
        if limit is None:
            limit = self._default_limit()
        self.limit = limit
        self.model_init = {'pm1thv2t': limit, 'mups0': limit}


class ModelPM2THV1T(ChandraModel):
    """pm2thv1t spec contains no limits; limit must be passed explicitly."""
    def __init__(self, model_spec, limit=None):
        self.msid = 'pm2thv1t'
        self.limit_type = 'max'
        self.model_spec = model_spec
        self.all_limits, self.units = self._read_spec_limits()
        if limit is None:
            limit = self._default_limit()
        self.limit = limit
        self.model_init = {'pm2thv1t': limit, 'mups0': limit, 'mups1': limit}


class Model2CEAHVPT(ChandraModel):
    extra_dwell_msids = ['2imonst', '2sponst', '2s2onst', '224pcast', '215pcast', 'aoeclips']

    def __init__(self, model_spec, limit=None):
        self.msid = '2ceahvpt'
        self.limit_type = 'max'
        self.model_spec = model_spec
        self.all_limits, self.units = self._read_spec_limits()
        if limit is None:
            limit = self._default_limit()
        self.limit = limit
        self.model_init = {
            '2ceahvpt': limit, 'cea0': limit, 'cea1': limit,
            'eclipse': False, 'dpa_power': 0.0,
        }


# ---------------------------------------------------------------------------
# Registry: msid → model class
# ---------------------------------------------------------------------------

MODELS = {
    'aacccdpt': ModelAACCCDPT,
    '1deamzt':  Model1DEAMZT,
    '1dpamzt':  Model1DPAMZT,
    'fptemp':   ModelFPTEMP,
    '1pdeaat':  Model1PDEAAT,
    'pftank2t': ModelPFTANK2T,
    '4rt700t':  Model4RT700T,
    'tpc_fsse': ModelTPC_FSSE,
    'tpcm_rw5': ModelTPCM_RW5,
    'pline03t': ModelPLINE03T,
    'pline04t': ModelPLINE04T,
    'pm1thv2t': ModelPM1THV2T,
    'pm2thv1t': ModelPM2THV1T,
    '2ceahvpt': Model2CEAHVPT,
}


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def export_result(result, path):
    """Write a ModelResult to a gzip-compressed JSON file.

    The file contains all time-series data (times as CXC seconds, predicted and
    observed temperatures, residuals), summary statistics, violation details, and
    model spec provenance (MD5, GitHub URL, release).  NaN and inf values are
    serialized as JSON null.  The output is compact (no whitespace) before
    compression.

    Parameters
    ----------
    result : ModelResult
    path : str
        Output path.  A '.json.gz' extension is conventional.
    """
    from cxotime import CxoTime

    def _array(arr):
        return [v if np.isfinite(v) else None for v in arr.tolist()]

    def _float(v):
        return None if not np.isfinite(v) else float(v)

    def _to_python(val):
        """Convert a numpy scalar or kadi set-like value to a JSON-serializable Python type."""
        if hasattr(val, 'item'):
            return val.item()
        if isinstance(val, str):
            return val
        try:
            return sorted(str(v) for v in val)  # handles TransKeysSet and other iterables
        except TypeError:
            return str(val)

    def _serialize_metadata(meta):
        """dict[int → structured ndarray] → dict[str → list of dicts]"""
        out = {}
        for bin_idx, arr in meta.items():
            rows = []
            for row in arr:
                rows.append({name: _to_python(row[name]) for name in arr.dtype.names})
            out[str(bin_idx)] = rows
        return out

    def _serialize_segments(segs):
        """dict[int → list of zip((reltimes, vals))] → dict[str → list of [[t, v], ...]]"""
        return {
            str(bin_idx): [[[float(t), _float(v)] for t, v in seg] for seg in seg_list]
            for bin_idx, seg_list in segs.items()
        }

    def _serialize_segment_norm(sn):
        return {str(bin_idx): [float(v) for v in vals] for bin_idx, vals in sn.items()}

    def _input_array(arr):
        if np.issubdtype(arr.dtype, np.floating):
            return [v if np.isfinite(v) else None for v in arr.tolist()]
        if arr.dtype == np.bool_:
            return arr.astype(np.int8).tolist()
        return arr.tolist()


    viol = result.violations()

    payload = {
        # --- model identity and limits ---
        'msid':                result.msid,
        'limit':               result.limit,
        'limit_type':          result.limit_type,
        'all_limits':          result.all_limits,
        'units':               result.units,
        # --- spec provenance ---
        'spec_md5':            result.spec_md5,
        'spec_github_url':     result.spec_github_url,
        'spec_github_release': result.spec_github_release,
        # --- full time series ---
        'datestart':           CxoTime(result.times[0]).date,
        'datestop':            CxoTime(result.times[-1]).date,
        'times':               result.times.tolist(),
        'predicted':           _array(result.predicted),
        'observed':            _array(result.observed),
        'residuals':           _array(result.residuals),
        # --- overall residual statistics and limit violations ---
        'stats':               result.stats(),
        'violations': {
            'count':    viol['count'],
            'fraction': viol['fraction'],
            'times':    viol['times'].tolist(),
            'values':   _array(viol['values']),
        },
        # --- pitch-binned segment traces and per-bin statistics ---
        'pitch_analysis': {
            'plist':       result.plist,
            'telem_bounds': list(result.telem_bounds),
            'metadata':    _serialize_metadata(result.metadata),
            'telem_segments': _serialize_segments(result.telem_segments),
            'err_segments':   _serialize_segments(result.err_segments),
            'segment_norm':   _serialize_segment_norm(result.segment_norm),
            'pitch_bin_statistics': {
                str(k): v for k, v in result.pitch_bin_statistics.items()
            },
        },
        # --- per-dwell analytics table ---
        'dwell_table': result.dwell_table,
        # --- high-level analytics ---
        'analytics': result.analytics,
        # --- solarheat model parameters ---
        'solar_heat_components': result.solar_heat_components,
        # --- dpa power lookup table (empty dict if not used by this model) ---
        'dpa_power': result.dpa_power,
        # --- model component inputs (all components with dvals, clipped to window) ---
        'inputs': {name: _input_array(arr) for name, arr in result.inputs.items()},
    }

    encoded = json.dumps(payload, separators=(',', ':')).encode('utf-8')
    with gzip.open(path, 'wb') as f:
        f.write(encoded)


def run_all_models(tstart, tstop, outdir, models_root, limit_overrides=None, models=None, spec_overrides=None):
    """Evaluate models over a time range and write results to gzip JSON files.

    Iterates over MODELS (or a subset), runs evaluate(tstart, tstop), and writes
    the result to ``{outdir}/{msid}.json.gz``.  Failures are logged and collected
    rather than raised so that one broken model does not abort the rest.

    Intended to be called by a cron job or the ``chandra-model-eval`` CLI.
    Configure a handler on the root or package logger to capture output
    (e.g. ``logging.FileHandler`` or systemd journal).

    Parameters
    ----------
    tstart : str
        Start time in any xija-accepted format (e.g. '2025:001').
    tstop : str
        Stop time.
    outdir : str
        Directory where output .json.gz files are written.  Created if absent.
    models_root : str
        Root path of the chandra_models repository checkout.  Model spec paths
        are constructed as ``{models_root}/{MODEL_SPECS[msid]}``.
    limit_overrides : dict, optional
        MSID → explicit limit value.  Required for models whose spec files
        contain no planning.warning limit (currently only 'pm2thv1t').
        Any model can be overridden here; others use the spec default.
    models : list of str, optional
        Subset of MSIDs to evaluate.  Defaults to all 14 models in MODELS.
    spec_overrides : dict, optional
        MSID → absolute path to a spec JSON file.  When present for an MSID,
        the supplied path is used instead of the default
        ``{models_root}/{MODEL_SPECS[msid]}`` path.

    Returns
    -------
    dict
        ``{'succeeded': [msid, ...], 'failed': {msid: error_message, ...}}``
    """
    if limit_overrides is None:
        limit_overrides = {}
    if spec_overrides is None:
        spec_overrides = {}

    os.makedirs(outdir, exist_ok=True)

    model_items = (
        [(msid, MODELS[msid]) for msid in models]
        if models is not None
        else list(MODELS.items())
    )

    succeeded = []
    failed = {}

    for msid, cls in model_items:
        spec_path = spec_overrides.get(msid) or os.path.join(models_root, MODEL_SPECS[msid])
        out_path = os.path.join(outdir, f'{msid}.json.gz')
        try:
            logger.info('running %s (%s to %s)', msid, tstart, tstop)
            model = cls(model_spec=spec_path, limit=limit_overrides.get(msid))
            result = model.evaluate(tstart, tstop)
            export_result(result, out_path)
            logger.info('wrote %s', out_path)
            succeeded.append(msid)
        except Exception as exc:
            logger.error('failed %s: %s', msid, exc, exc_info=True)
            failed[msid] = str(exc)

    logger.info(
        'run_all_models complete: %d succeeded, %d failed',
        len(succeeded), len(failed),
    )
    return {'succeeded': succeeded, 'failed': failed}
