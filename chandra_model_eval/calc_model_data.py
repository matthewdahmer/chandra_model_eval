import re
from datetime import datetime, timezone

import numpy as np
import xija
from kadi.commands import states


def get_npnt_state_data(tstart, tstop):
    """ Get states where 'vid_board', 'clocking', 'fep_count', 'pcad_mode' are constant.
    Args:
        tstart (int, float, string): Start time, using Ska time epoch
        tstop (int, float, string): Stop time, using Ska time epoch

    Returns:
        (numpy.ndarray): state data
    """

    keys = ['pitch', 'off_nom_roll', 'ccd_count', 'fep_count', 'clocking', 'vid_board', 'pcad_mode', 'simpos', 'off_nom_roll']

    try:
        state_data = states.get_states(tstart, tstop, state_keys=keys, merge_identical=True)
    except OSError as e:
        import errno
        if e.errno == errno.ENETUNREACH:
            state_data = states.get_states(tstart, tstop, state_keys=keys, merge_identical=True, scenario="flight")
        else:
            raise

    # relying on 'pcad_mode' to ensure attitude does not change significantly within a dwell
    state_data = states.reduce_states(state_data, ['pcad_mode'], all_keys=True, merge_identical=True)

    return np.array(state_data[state_data['pcad_mode'] == 'NPNT'])


def bin_data_by_pitch(state_data, plist, times, telem, error, color_stat='start'):
    """ Bin telemetry and model error data by dwell pitch

    Args:
        (numpy.ndarray): state data
        plist (tuple, list, numpy.ndarray): bounding pitch values for all bins (e.g. [45, 75, 90, 120, 180])
        times (numpy.ndarray): times corresponding to telemetry and error arrays
        telem (numpy.ndarray): telemetry to separate into dwell pitch bins
        error (numpy.ndarray): model error values to separate into dwell pitch bins

    Returns:
        metadata, telem_segments, err_segments, segment_norm, telem_bounds

    Note:
        Telemetry and error are binned in the same loop since their arrays are expected to align and it is faster to
        work on both together than to call this function separately twice (since often both quantities are needed).
    """

    pind = np.digitize(state_data['pitch'], bins=plist)-1

    # pinds could be simply range(len(plist) - 1), except sometimes some bins won't
    # have any data, which will cause problems. Instead determine which bins actually
    # have data so we only use those.
    pinds = list(set(pind))
    pinds.sort()

    telem_segments = dict([(n, []) for n in pinds])
    metadata = dict([(n, []) for n in pinds])
    err_segments = dict([(n, []) for n in pinds])
    segment_norm = dict([(n, []) for n in pinds])
    telem_bounds = (float(np.nanmin(telem)), float(np.nanmax(telem)))
    vmin, vmax = telem_bounds
    norm_denom = (vmax - vmin) if vmax != vmin else 1.0

    for num in pinds:
        for s in state_data[pind==num]:
            tind = (times >= s['tstart']) & (times < s['tstop'])
            if any(tind) & ((s['tstop'] - s['tstart']) > 3600):
                reltimes = times[tind] - times[tind][0]
                temps = telem[tind]

                metadata[num].append(s)
                telem_segments[num].append(list(zip(reltimes, temps)))

                err = error[tind]
                err_segments[num].append(list(zip(reltimes, err)))

                segment_norm[num].append(float(np.clip((temps[0] - vmin) / norm_denom, 0.0, 1.0)))

        metadata[num] = np.array(metadata[num], dtype=s.dtype)

    return metadata, telem_segments, err_segments, segment_norm, telem_bounds


def get_pitch_midpoints(model):
    """ Get midpoints for solarheat pitch grid
    Args:
        model (xija.model.XijaModel): Xija model object

    Returns:
        (list): pitch bin boundaries derived from solarheat pitch grid; empty list if no solarheat params
    """
    r = re.compile(r'solarheat__.*__P.*_(\d+).*')
    pitches = [r.findall(d['full_name'])[0] for d in model.pars if r.findall(d['full_name'])]
    if not pitches:
        return []
    pitches = np.array(list(set(pitches)), dtype=float)
    pitches = np.sort(pitches)

    plist = list(pitches[:-1] + np.diff(pitches) / 2)
    plist.insert(0, float(pitches[0]))
    plist.append(float(pitches[-1]))

    return plist


def get_solarheat_components(model):
    """Extract solarheat component parameters as a list of structured dicts.

    Returns one dict per SolarHeat component (excluding SolarHeatOffNomRoll).
    Each dict groups P and dP pitch tables with the scalar parameters that govern
    how they are combined at run time: tau, ampl, epoch, and bias/dh_heater.

    For SolarHeat variants, P is a flat list aligned with P_pitches.
    For SimZDepSolarHeat variants (all_simz_, psmc_, hrc_is_acis_simz_, etc.),
    P is a dict keyed by instrument name (e.g. 'hrcs', 'hrci', 'aciss', 'acisi').
    dP is always a flat list (one shared curve per component).
    """
    components = []

    for comp in model.comp.values():
        name = str(comp)
        if 'solarheat' not in name or 'off_nom_roll' in name:
            continue

        entry = {
            'name': name,
            'node': comp.node.name,
            'class': type(comp).__name__,
            'epoch': comp.epoch,
            'tau': float(comp.tau),
            'ampl': float(comp.ampl),
            'P_pitches': comp.P_pitches.tolist(),
            'dP_pitches': comp.dP_pitches.tolist(),
        }

        if hasattr(comp, 'instr_names'):
            # SimZDepSolarHeat variant: one P curve per instrument, shared dP
            entry['dh_heater'] = float(comp.dh_heater)
            entry['P'] = {
                instr: comp.parvals[i * comp.n_p:(i + 1) * comp.n_p].tolist()
                for i, instr in enumerate(comp.instr_names)
            }
            entry['dP'] = comp.parvals[
                comp.n_instr * comp.n_p:comp.n_instr * comp.n_p + comp.n_dp
            ].tolist()
        else:
            # SolarHeat variant: single P curve
            n_dp = len(comp.dP_pitches)
            entry['bias'] = float(comp.bias)
            entry['P'] = comp.parvals[:comp.n_pitches].tolist()
            entry['dP'] = comp.parvals[comp.n_pitches:comp.n_pitches + n_dp].tolist()
            for bias_par in ('hrc_bias', 'hrci_bias', 'hrcs_bias'):
                if hasattr(comp, bias_par):
                    entry[bias_par] = float(getattr(comp, bias_par))

        components.append(entry)

    return components


def get_dpa_power_params(model):
    """Extract dpa_power component parameters from an xija model.

    Returns a dict with:
      'lookup' — pattern string (e.g. '0xxx', '1xx0') → power value (watts)
      'mult'   — multiplicative scale factor applied to the looked-up power
      'bias'   — constant offset added after scaling

    The pattern strings encode which instrument-state dimensions are relevant
    for each entry; 'x' is a wildcard. Position order is fep_count, ccd_count,
    vid_board, clocking (vid_board is always wildcarded in current models).

    Returns an empty dict if the model contains no dpa_power lookup parameters.
    """
    lookup = {}
    mult = None
    bias = None

    prefix = 'dpa_power__'
    for par in model.pars:
        full_name = par['full_name']
        if not full_name.startswith(prefix):
            continue
        param_name = full_name[len(prefix):]
        if param_name.startswith('pow_'):
            lookup[param_name[4:]] = float(par['val'])
        elif param_name == 'mult':
            mult = float(par['val'])
        elif param_name == 'bias':
            bias = float(par['val'])

    if not lookup:
        return {}

    result = {'lookup': lookup}
    if mult is not None:
        result['mult'] = mult
    if bias is not None:
        result['bias'] = bias
    return result


def _cxc_to_dt(cxc_secs):
    """Convert CXC seconds to UTC datetime objects (scalar or array)."""
    CXC_EPOCH_UNIX = 883612736.816
    unix = np.atleast_1d(np.asarray(cxc_secs, dtype=float)) + CXC_EPOCH_UNIX
    return np.array([datetime.fromtimestamp(u, tz=timezone.utc) for u in unix])


def _seg_stats(seg_list):
    """Compute summary statistics across all segments in a pitch bin.

    Returns a dict or None if no usable data. Fields:
        mean, std, rms, max_abs, p05, p25, p50, p75, p95
        segment_mean_mean, segment_mean_std
        segment_drift_mean, segment_drift_std  (units/ks; None if insufficient data)
    """
    all_vals = []
    seg_means = []
    seg_slopes = []

    for seg in seg_list:
        if not seg:
            continue
        times = np.array([t for t, v in seg], dtype=float)
        vals = np.array([v for t, v in seg], dtype=float)
        finite = np.isfinite(vals)
        if np.sum(finite) < 2:
            continue
        vals_f = vals[finite]
        times_f = times[finite]
        all_vals.append(vals_f)
        seg_means.append(float(np.mean(vals_f)))
        duration = times_f[-1] - times_f[0]
        if duration > 0:
            seg_slopes.append(float(np.polyfit(times_f / 1000.0, vals_f, 1)[0]))

    if not all_vals:
        return None

    pooled = np.concatenate(all_vals)
    return {
        'mean':               float(np.mean(pooled)),
        'std':                float(np.std(pooled)),
        'rms':                float(np.sqrt(np.mean(pooled ** 2))),
        'max_abs':            float(np.max(np.abs(pooled))),
        'p05':                float(np.percentile(pooled, 5)),
        'p25':                float(np.percentile(pooled, 25)),
        'p50':                float(np.percentile(pooled, 50)),
        'p75':                float(np.percentile(pooled, 75)),
        'p95':                float(np.percentile(pooled, 95)),
        'segment_mean_mean':  float(np.mean(seg_means)),
        'segment_mean_std':   float(np.std(seg_means)),
        'segment_drift_mean': float(np.mean(seg_slopes)) if seg_slopes else None,
        'segment_drift_std':  float(np.std(seg_slopes)) if seg_slopes else None,
    }


def compute_pitch_bin_statistics(telem_segments, err_segments):
    """Compute per-pitch-bin statistics for both telemetry and model error.

    Returns a dict keyed by bin index. Each value is a dict with:
        n_segments  : number of dwells in this bin
        n_points    : total time steps across all segments
        telem       : statistics dict for observed temperature (see _seg_stats)
        error       : statistics dict for model error / residual (see _seg_stats)
    or None if the bin has no usable data.
    """
    all_bins = set(list(telem_segments.keys()) + list(err_segments.keys()))
    result = {}
    for bin_idx in all_bins:
        t_segs = telem_segments.get(bin_idx, [])
        e_segs = err_segments.get(bin_idx, [])
        t_stats = _seg_stats(t_segs)
        e_stats = _seg_stats(e_segs)
        if t_stats is None and e_stats is None:
            result[bin_idx] = None
            continue
        n_segments = len([s for s in t_segs if s]) if t_segs else 0
        n_points = int(sum(
            np.sum(np.isfinite([v for t, v in seg])) for seg in t_segs if seg
        ))
        result[bin_idx] = {
            'n_segments': n_segments,
            'n_points':   n_points,
            'telem':      t_stats,
            'error':      e_stats,
        }
    return result


def build_dwell_table(metadata, telem_segments, err_segments, segment_norm, telem_bounds,
                      dist_sat_earth=None, extra_msid_data=None):
    """Build a per-dwell analytics table from pitch-binned segment data.

    Each dwell that passed the >1 hr filter in bin_data_by_pitch contributes one row.
    Dwells are sorted chronologically by tstart.

    Args:
        metadata          : dict[bin_idx → numpy structured array] from bin_data_by_pitch
        telem_segments    : dict[bin_idx → list of [(relt_s, temp), ...]]
        err_segments      : dict[bin_idx → list of [(relt_s, err), ...]]
        segment_norm      : dict[bin_idx → list of floats] (normalised starting temp)
        telem_bounds      : (t_min, t_max) tuple
        dist_sat_earth    : optional (times_array, vals_array) from cheta for dist_sat_earth
        extra_msid_data   : optional dict of {msid_name: (times_array, vals_array)} for
                            per-dwell median values of additional MSIDs

    Returns:
        dict of parallel lists, one entry per dwell.
    """
    from cxotime import CxoTime

    t_min, t_max = telem_bounds
    t_range = t_max - t_min

    base_keys = [
        'tstart', 'pitch', 'simpos', 'fep_count', 'ccd_count', 'clocking', 'off_nom_roll',
        'obs_start_temp', 'obs_max_temp', 'obs_mean_temp',
        'err_mean', 'err_max_abs', 'err_p95', 'err_end',
        'n_points', 'pitch_bin',
    ]
    if dist_sat_earth is not None:
        base_keys.append('dist_satearth')
    if extra_msid_data:
        base_keys.extend(sorted(extra_msid_data.keys()))

    rows = {k: [] for k in base_keys}

    # Pre-unpack dist_sat_earth arrays for fast lookup
    dse_times = dse_vals = None
    if dist_sat_earth is not None:
        dse_times, dse_vals = dist_sat_earth

    for bin_idx in sorted(metadata.keys(), key=lambda k: int(k)):
        dwells = metadata[bin_idx]
        t_segs = telem_segments.get(bin_idx, [])
        e_segs = err_segments.get(bin_idx, [])
        norms = segment_norm.get(bin_idx, [])

        for i in range(len(dwells)):
            if i >= len(t_segs) or i >= len(e_segs):
                continue

            tel_pairs = t_segs[i]
            err_pairs = e_segs[i]
            if len(tel_pairs) < 2 or len(err_pairs) < 2:
                continue

            tel_vals = np.array([v for _, v in tel_pairs], dtype=float)
            err_vals = np.array([v for _, v in err_pairs], dtype=float)

            tel_finite = np.isfinite(tel_vals)
            err_finite = np.isfinite(err_vals)
            if np.sum(tel_finite) < 2 or np.sum(err_finite) < 2:
                continue

            tel_f = tel_vals[tel_finite]
            err_f = err_vals[err_finite]
            n = len(err_f)

            tail_start = max(1, int(0.8 * n))
            err_end = float(np.mean(err_f[tail_start:]))

            norm_val = norms[i] if i < len(norms) else float(np.clip((tel_f[0] - t_min) / t_range, 0, 1) if t_range else 0)
            obs_start = float(t_min + norm_val * t_range)

            dwell = dwells[i]
            tstart = float(dwell['tstart'])
            tstop = float(dwell['tstop'])

            rows['tstart'].append(tstart)
            rows['pitch'].append(float(dwell['pitch']))
            rows['simpos'].append(int(dwell['simpos']))
            rows['fep_count'].append(int(dwell['fep_count']))
            rows['ccd_count'].append(int(dwell['ccd_count']))
            rows['clocking'].append(int(dwell['clocking']))
            rows['off_nom_roll'].append(float(dwell['off_nom_roll']))
            rows['obs_start_temp'].append(obs_start)
            rows['obs_max_temp'].append(float(np.max(tel_f)))
            rows['obs_mean_temp'].append(float(np.mean(tel_f)))
            rows['err_mean'].append(float(np.mean(err_f)))
            rows['err_max_abs'].append(float(np.max(np.abs(err_f))))
            rows['err_p95'].append(float(np.percentile(np.abs(err_f), 95)))
            rows['err_end'].append(err_end)
            rows['n_points'].append(n)
            rows['pitch_bin'].append(int(bin_idx))

            # dist_sat_earth at dwell start: nearest sample at or before tstart
            if dse_times is not None and len(dse_times) > 0:
                idx = np.searchsorted(dse_times, tstart, side='right') - 1
                idx = max(0, min(idx, len(dse_vals) - 1))
                rows['dist_satearth'].append(float(dse_vals[idx]))

            # Extra MSIDs: median (numeric) or mode (string/categorical) over dwell
            if extra_msid_data:
                for msid_name, (mt, mv) in extra_msid_data.items():
                    mask = (mt >= tstart) & (mt <= tstop)
                    vals_in_dwell = mv[mask]
                    if len(vals_in_dwell) == 0:
                        rows[msid_name].append(None)
                    elif np.issubdtype(vals_in_dwell.dtype, np.number):
                        finite = vals_in_dwell[np.isfinite(vals_in_dwell)]
                        rows[msid_name].append(float(np.median(finite)) if len(finite) > 0 else None)
                    else:
                        from collections import Counter
                        c = Counter(vals_in_dwell.tolist())
                        rows[msid_name].append(c.most_common(1)[0][0] if c else None)

    if not rows['tstart']:
        rows['datestart'] = []
        return rows

    # Sort by time
    order = np.argsort(rows['tstart'])
    for k in rows:
        rows[k] = np.array(rows[k], dtype=object)[order].tolist()

    rows['datestart'] = CxoTime(np.array(rows['tstart'])).date.tolist()

    return rows


def compute_analytics(dwell_table, telem_bounds, limit, limit_type):
    """Compute high-level model performance analytics from the per-dwell table.

    Args:
        dwell_table : dict of parallel lists from build_dwell_table
        telem_bounds: (t_min, t_max) tuple of observed temperature range
        limit       : planning warning limit
        limit_type  : 'max' or 'min'

    Returns a dict with:
        near_limit_threshold
            Temperature threshold defining "near-limit" dwells.
            For max-limit models: top third of observed range (obs_mean_temp >= threshold).
            For min-limit models: bottom third of observed range (obs_mean_temp <= threshold).
        monthly
            Per-calendar-month weighted mean bias and std, for all dwells and near-limit dwells.
            Weights are dwell n_points. Keys: "all", "near_limit".
            Each value: {"months": ["YYYY-MM", ...], "mean": [...], "std": [...], "n": [...]}
        error_by_temperature
            Model error binned by observed mean temperature across 20 equal bins spanning
            telem_bounds. Useful for identifying temperature-dependent bias.
            Keys: bin_edges, bin_centers, mean, std, p95_abs, n
        period_comparison
            Error statistics for the early 2/3 vs recent 1/3 of the evaluation period,
            for all dwells and near-limit dwells separately.
            Keys: split_date, all, near_limit
            Each subset: {"early": {n, mean, std, p95_abs}, "recent": {...}}
    """
    if not dwell_table.get('tstart'):
        return {}

    tstart = np.array(dwell_table['tstart'])
    obs_mean = np.array(dwell_table['obs_mean_temp'])
    err_mean = np.array(dwell_table['err_mean'])
    err_max_abs = np.array(dwell_table['err_max_abs'])
    n_points = np.array(dwell_table['n_points'])

    t_min, t_max = telem_bounds
    obs_range = t_max - t_min

    # Near-limit threshold: top/bottom third of observed temperature range
    if limit_type == 'max':
        near_limit_threshold = float(t_min + obs_range * (2.0 / 3.0))
        near_limit_mask = obs_mean >= near_limit_threshold
    else:
        near_limit_threshold = float(t_min + obs_range * (1.0 / 3.0))
        near_limit_mask = obs_mean <= near_limit_threshold

    # Monthly stats
    dts = _cxc_to_dt(tstart)

    def _monthly(mask):
        if mask.sum() == 0:
            return {'months': [], 'mean': [], 'std': [], 'n': []}
        buckets = {}
        for dt, e, w in zip(dts[mask], err_mean[mask], n_points[mask]):
            key = f'{dt.year}-{dt.month:02d}'
            buckets.setdefault(key, {'err': [], 'w': []})
            buckets[key]['err'].append(float(e))
            buckets[key]['w'].append(float(w))
        out = {'months': [], 'mean': [], 'std': [], 'n': []}
        for ym in sorted(buckets):
            vals = np.array(buckets[ym]['err'])
            wts = np.array(buckets[ym]['w'])
            wt_mean = float(np.average(vals, weights=wts))
            wt_std = float(np.sqrt(np.average((vals - wt_mean) ** 2, weights=wts)))
            out['months'].append(ym)
            out['mean'].append(round(wt_mean, 6))
            out['std'].append(round(wt_std, 6))
            out['n'].append(len(vals))
        return out

    all_mask = np.ones(len(tstart), dtype=bool)
    monthly = {
        'all':        _monthly(all_mask),
        'near_limit': _monthly(near_limit_mask),
    }

    # Error by temperature — 20 equal bins across observed range
    n_bins = 20
    edges = np.linspace(t_min, t_max, n_bins + 1)
    centers = 0.5 * (edges[:-1] + edges[1:])
    e_by_t = {'bin_edges': edges.tolist(), 'bin_centers': centers.tolist(),
               'mean': [], 'std': [], 'p95_abs': [], 'n': []}
    for lo, hi in zip(edges[:-1], edges[1:]):
        mask = (obs_mean >= lo) & (obs_mean < hi)
        if mask.sum() == 0:
            e_by_t['mean'].append(None)
            e_by_t['std'].append(None)
            e_by_t['p95_abs'].append(None)
            e_by_t['n'].append(0)
        else:
            e = err_mean[mask]
            e_by_t['mean'].append(round(float(np.mean(e)), 6))
            e_by_t['std'].append(round(float(np.std(e)), 6))
            e_by_t['p95_abs'].append(round(float(np.percentile(np.abs(e), 95)), 6))
            e_by_t['n'].append(int(mask.sum()))

    # Period comparison — early 2/3 vs recent 1/3 by dwell count
    n_dwells = len(tstart)
    split_idx = int(n_dwells * 2 / 3)
    t_split = float(tstart[split_idx]) if split_idx < n_dwells else float(tstart[-1])
    early_mask = tstart < t_split
    recent_mask = ~early_mask

    from cxotime import CxoTime
    split_date = CxoTime(t_split).date if n_dwells > 1 else None

    def _period(mask):
        if mask.sum() == 0:
            return {'n': 0, 'mean': None, 'std': None, 'p95_abs': None}
        e = err_mean[mask]
        return {
            'n':       int(mask.sum()),
            'mean':    round(float(np.mean(e)), 6),
            'std':     round(float(np.std(e)), 6),
            'p95_abs': round(float(np.percentile(np.abs(e), 95)), 6),
        }

    period_comparison = {
        'split_date': split_date,
        'all': {
            'early':  _period(early_mask),
            'recent': _period(recent_mask),
        },
        'near_limit': {
            'early':  _period(early_mask & near_limit_mask),
            'recent': _period(recent_mask & near_limit_mask),
        },
    }

    return {
        'near_limit_threshold': near_limit_threshold,
        'monthly':              monthly,
        'error_by_temperature': e_by_t,
        'period_comparison':    period_comparison,
    }


if __name__ == "__main__":

    # Read in model
    tstart = '2025:001:00:00:00'
    tstop = '2026:090:00:00:00'

    model = xija.ThermalModel('hrc', start=tstart, stop=tstop, model_spec='/Users/matthewdahmer/AXAFLIB/chandra_models/chandra_models/xija/hrc/cea_spec.json')
    plist = get_pitch_midpoints(model)
    state_data = get_npnt_state_data(tstart, tstop)

    metadata, telem_segments, err_segments, segment_norm, telem_bounds = bin_data_by_pitch(
        state_data,
        plist,
        times,
        telem,
        error
    )
