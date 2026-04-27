"""
2ceahvpt model error trend analysis.

Focuses on two issues:
  1. Error growth over time (especially recent months)
  2. Error behavior specifically at high temperatures (near the 9.5 degC planning limit)

Data sources used (no raw time-series arrays read):
  - metadata: per-dwell timestamps, pitch, ACIS config, simpos
  - err_segments: [relative_time_s, error] pairs per dwell per pitch bin
  - telem_segments: [relative_time_s, observed_temp] pairs per dwell per pitch bin
  - segment_norm: normalized starting temp per dwell (scaled to telem_bounds)
  - stats: overall summary statistics
  - telem_bounds: [min, max] of all observed telemetry

Usage:
  python analyze_model_error.py [path/to/2ceahvpt.json.gz]
"""

import gzip
import json
import sys
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------

def load_data(path):
    with gzip.open(path, "rt") as f:
        return json.load(f)


def cxc_to_datetime(cxc_secs):
    """Convert CXC seconds (since 1998-01-01 TT) to UTC datetime."""
    cxc_epoch_unix = 883612736.816  # 1998-01-01 00:00:00 TT in Unix seconds
    unix = np.atleast_1d(np.asarray(cxc_secs, dtype=float)) + cxc_epoch_unix
    return np.array([datetime.fromtimestamp(u, tz=timezone.utc) for u in unix])


# ---------------------------------------------------------------------------
# Build per-dwell table
# ---------------------------------------------------------------------------

def build_dwell_table(data):
    """
    Returns a dict of parallel arrays, one entry per dwell:
      tstart          CXC seconds
      dt              datetime (UTC)
      pitch           mean pitch angle
      simpos          SIM-Z position
      obs_start_temp  observed temperature at dwell start (degC)
      obs_max_temp    maximum observed temperature during dwell
      obs_mean_temp   mean observed temperature during dwell
      err_mean        mean residual (observed - predicted) over dwell
      err_max_abs     largest absolute residual over dwell
      err_p95         95th percentile of |residual| over dwell
      err_end         mean error in last 20% of dwell (drift indicator)
      n_points        number of time steps in dwell
      pitch_bin       pitch bin index
    """
    rows = {k: [] for k in [
        "tstart", "dt", "pitch", "simpos",
        "obs_start_temp", "obs_max_temp", "obs_mean_temp",
        "err_mean", "err_max_abs", "err_p95", "err_end",
        "n_points", "pitch_bin",
    ]}

    telem_bounds = data["telem_bounds"]
    t_min, t_max = telem_bounds[0], telem_bounds[1]
    t_range = t_max - t_min

    meta = data["metadata"]
    err_segs = data["err_segments"]
    telem_segs = data["telem_segments"]
    seg_norm = data["segment_norm"]

    for bin_key in sorted(meta.keys(), key=int):
        bin_idx = int(bin_key)
        dwells = meta[bin_key]
        errs = err_segs[bin_key]
        telems = telem_segs[bin_key]
        norms = seg_norm[bin_key]

        for i, dwell in enumerate(dwells):
            if i >= len(errs) or i >= len(telems):
                continue

            err_arr = np.array(errs[i])        # shape (n, 2): [rel_t, err]
            tel_arr = np.array(telems[i])      # shape (n, 2): [rel_t, temp]

            if err_arr.ndim != 2 or len(err_arr) < 2:
                continue
            if tel_arr.ndim != 2 or len(tel_arr) < 2:
                continue

            err_vals = err_arr[:, 1]
            tel_vals = tel_arr[:, 1]
            n = len(err_vals)

            # Tail = last 20% of dwell
            tail_start = max(1, int(0.8 * n))
            err_end = float(np.mean(err_vals[tail_start:]))

            # Starting temp from segment_norm (normalized starting temp)
            obs_start = t_min + norms[i] * t_range

            rows["tstart"].append(dwell["tstart"])
            rows["dt"].append(cxc_to_datetime(dwell["tstart"])[0])
            rows["pitch"].append(dwell["pitch"])
            rows["simpos"].append(dwell["simpos"])
            rows["obs_start_temp"].append(obs_start)
            rows["obs_max_temp"].append(float(np.max(tel_vals)))
            rows["obs_mean_temp"].append(float(np.mean(tel_vals)))
            rows["err_mean"].append(float(np.mean(err_vals)))
            rows["err_max_abs"].append(float(np.max(np.abs(err_vals))))
            rows["err_p95"].append(float(np.percentile(np.abs(err_vals), 95)))
            rows["err_end"].append(err_end)
            rows["n_points"].append(n)
            rows["pitch_bin"].append(bin_idx)

    for k in rows:
        if k != "dt":
            rows[k] = np.array(rows[k])
    rows["dt"] = np.array(rows["dt"])

    # Sort by time
    order = np.argsort(rows["tstart"])
    for k in rows:
        rows[k] = rows[k][order]

    return rows


# ---------------------------------------------------------------------------
# Monthly binning utility
# ---------------------------------------------------------------------------

def monthly_stats(dts, values, weights=None):
    """Group (dt, value) pairs by calendar month; return (month_centers, mean, std, n)."""
    if weights is None:
        weights = np.ones(len(values))

    months = {}
    for dt, v, w in zip(dts, values, weights):
        key = (dt.year, dt.month)
        months.setdefault(key, []).append((v, w))

    centers, means, stds, ns = [], [], [], []
    for (yr, mo), pairs in sorted(months.items()):
        vals, wts = zip(*pairs)
        vals = np.array(vals)
        wts = np.array(wts)
        wt_mean = np.average(vals, weights=wts)
        wt_std = np.sqrt(np.average((vals - wt_mean) ** 2, weights=wts))
        centers.append(datetime(yr, mo, 15, tzinfo=timezone.utc))
        means.append(wt_mean)
        stds.append(wt_std)
        ns.append(len(vals))

    return (np.array(centers), np.array(means),
            np.array(stds), np.array(ns))


# ---------------------------------------------------------------------------
# Plotting
# ---------------------------------------------------------------------------

LIMIT = 9.5           # planning.warning.high (degC)
HIGH_TEMP_THRESH = 6.0  # "high temperature" regime: obs_mean_temp above this

MONTH_FMT = mdates.DateFormatter("%Y-%m")


def _add_recent_shade(ax, dts, months=6):
    """Shade the most-recent N months to highlight the period of concern."""
    t_end = max(dts)
    # approximate N months back
    yr = t_end.year
    mo = t_end.month - months
    if mo <= 0:
        yr -= 1
        mo += 12
    t_start = datetime(yr, mo, 1, tzinfo=timezone.utc)
    ax.axvspan(t_start, t_end, color="gold", alpha=0.18, label=f"Last {months} months")


def fig1_overall_monthly_bias(dws, stats):
    """Monthly mean bias over the full evaluation period, split by temperature regime."""
    fig, axes = plt.subplots(2, 1, figsize=(12, 8), sharex=True)
    fig.suptitle("2CEAHVPT  |  Monthly Mean Model Bias (observed − predicted)",
                 fontsize=13, fontweight="bold")

    hi_mask = dws["obs_mean_temp"] >= HIGH_TEMP_THRESH
    lo_mask = ~hi_mask

    for ax, mask, label, color in [
        (axes[0], np.ones(len(dws["dt"]), dtype=bool), "All dwells", "steelblue"),
        (axes[1], hi_mask, f"High-temp dwells (obs_mean ≥ {HIGH_TEMP_THRESH} °C)", "crimson"),
    ]:
        if mask.sum() == 0:
            ax.text(0.5, 0.5, "No data", transform=ax.transAxes, ha="center")
            continue

        mc, mm, ms, mn = monthly_stats(
            dws["dt"][mask], dws["err_mean"][mask],
            weights=dws["n_points"][mask],
        )
        _add_recent_shade(ax, dws["dt"])

        ax.fill_between(mc, mm - ms, mm + ms, alpha=0.2, color=color)
        ax.plot(mc, mm, "o-", color=color, lw=1.8, ms=5, label=label)
        ax.axhline(0, color="k", lw=0.8, ls="--")

        # Overall mean reference line from stats
        ax.axhline(stats["mean"], color="gray", lw=1.0, ls=":", label=f"Overall mean ({stats['mean']:.3f} °C)")

        ax.set_ylabel("Mean bias (°C)")
        ax.legend(fontsize=8, loc="upper left")
        ax.grid(True, alpha=0.3)
        ax.xaxis.set_major_formatter(MONTH_FMT)

    axes[1].set_xlabel("Month")
    fig.autofmt_xdate(rotation=30)
    fig.tight_layout()
    return fig


def fig2_high_temp_error_vs_time(dws):
    """Scatter of per-dwell mean error vs time, colored by observed temperature."""
    fig, ax = plt.subplots(figsize=(13, 5))
    fig.suptitle("2CEAHVPT  |  Per-Dwell Mean Error vs Time  (colored by observed temperature)",
                 fontsize=12, fontweight="bold")

    sc = ax.scatter(
        dws["dt"], dws["err_mean"],
        c=dws["obs_mean_temp"],
        cmap="RdYlBu_r", vmin=1, vmax=LIMIT,
        s=12, alpha=0.6, linewidths=0,
    )
    cb = fig.colorbar(sc, ax=ax, label="Mean observed temp (°C)")
    cb.ax.axhline(HIGH_TEMP_THRESH, color="k", lw=1.2, ls="--")
    cb.ax.text(1.05, HIGH_TEMP_THRESH / LIMIT, f"{HIGH_TEMP_THRESH} °C",
               transform=cb.ax.transAxes, va="center", fontsize=8)

    _add_recent_shade(ax, dws["dt"])
    ax.axhline(0, color="k", lw=0.9, ls="--")
    ax.set_ylabel("Mean bias per dwell (°C)")
    ax.set_xlabel("Date")
    ax.xaxis.set_major_formatter(MONTH_FMT)
    ax.grid(True, alpha=0.3)
    fig.autofmt_xdate(rotation=30)
    fig.tight_layout()
    return fig


def fig3_rolling_rms_by_temp_regime(dws, window=60):
    """Rolling RMS of error (by dwell count) for high-temp vs all dwells."""
    fig, ax = plt.subplots(figsize=(12, 5))
    fig.suptitle(f"2CEAHVPT  |  Rolling RMS Error  (window = {window} dwells)",
                 fontsize=12, fontweight="bold")

    def rolling_rms(vals, w):
        out = np.full(len(vals), np.nan)
        for i in range(w - 1, len(vals)):
            out[i] = np.sqrt(np.mean(vals[i - w + 1:i + 1] ** 2))
        return out

    dt_all = dws["dt"]
    err_all = dws["err_mean"]
    rms_all = rolling_rms(err_all, window)
    ax.plot(dt_all, rms_all, lw=1.5, color="steelblue", label="All dwells")

    hi_mask = dws["obs_mean_temp"] >= HIGH_TEMP_THRESH
    if hi_mask.sum() >= window:
        dt_hi = dws["dt"][hi_mask]
        err_hi = dws["err_mean"][hi_mask]
        rms_hi = rolling_rms(err_hi, window)
        ax.plot(dt_hi, rms_hi, lw=1.8, color="crimson",
                label=f"High-temp dwells (≥ {HIGH_TEMP_THRESH} °C)")

    _add_recent_shade(ax, dws["dt"])
    ax.set_ylabel("RMS error (°C)")
    ax.set_xlabel("Date")
    ax.xaxis.set_major_formatter(MONTH_FMT)
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3)
    fig.autofmt_xdate(rotation=30)
    fig.tight_layout()
    return fig


def fig4_error_vs_temperature(dws):
    """
    Bin dwells by observed temperature; show mean bias and 95th percentile of
    |error| per bin. Highlights the limit.
    """
    fig, axes = plt.subplots(1, 2, figsize=(13, 5))
    fig.suptitle("2CEAHVPT  |  Model Error vs Observed Temperature",
                 fontsize=12, fontweight="bold")

    temp_bins = np.arange(0, 13.5, 1.0)
    bin_centers = 0.5 * (temp_bins[:-1] + temp_bins[1:])
    obs_temp = dws["obs_mean_temp"]

    mean_by_bin, std_by_bin, p95_by_bin, n_by_bin = [], [], [], []
    for lo, hi in zip(temp_bins[:-1], temp_bins[1:]):
        mask = (obs_temp >= lo) & (obs_temp < hi)
        if mask.sum() == 0:
            mean_by_bin.append(np.nan)
            std_by_bin.append(np.nan)
            p95_by_bin.append(np.nan)
            n_by_bin.append(0)
        else:
            e = dws["err_mean"][mask]
            mean_by_bin.append(np.mean(e))
            std_by_bin.append(np.std(e))
            p95_by_bin.append(np.percentile(np.abs(e), 95))
            n_by_bin.append(mask.sum())

    mean_by_bin = np.array(mean_by_bin)
    std_by_bin = np.array(std_by_bin)
    p95_by_bin = np.array(p95_by_bin)
    n_by_bin = np.array(n_by_bin)

    ax = axes[0]
    ax.fill_between(bin_centers, mean_by_bin - std_by_bin,
                    mean_by_bin + std_by_bin, alpha=0.2, color="steelblue")
    ax.plot(bin_centers, mean_by_bin, "o-", color="steelblue", lw=1.8, ms=6)
    ax.axhline(0, color="k", lw=0.8, ls="--")
    ax.axvline(LIMIT, color="red", lw=1.2, ls="--", label=f"Limit {LIMIT} °C")
    ax.axvline(HIGH_TEMP_THRESH, color="orange", lw=1.0, ls=":", label=f"High-temp threshold {HIGH_TEMP_THRESH} °C")
    ax.set_xlabel("Mean observed temperature (°C)")
    ax.set_ylabel("Mean bias (°C)  [obs − pred]")
    ax.set_title("Mean bias by temperature")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)

    ax = axes[1]
    ax.bar(bin_centers, p95_by_bin, width=0.8, color="steelblue", alpha=0.7)
    ax.axvline(LIMIT, color="red", lw=1.2, ls="--", label=f"Limit {LIMIT} °C")
    ax.axvline(HIGH_TEMP_THRESH, color="orange", lw=1.0, ls=":", label=f"High-temp threshold {HIGH_TEMP_THRESH} °C")
    ax.set_xlabel("Mean observed temperature (°C)")
    ax.set_ylabel("95th pct |error| per dwell (°C)")
    ax.set_title("Error spread by temperature")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)

    # secondary axis for dwell count
    ax2 = axes[1].twinx()
    ax2.plot(bin_centers, n_by_bin, "s--", color="gray", ms=4, lw=1, alpha=0.6)
    ax2.set_ylabel("N dwells (gray)", color="gray")
    ax2.tick_params(axis="y", colors="gray")

    fig.tight_layout()
    return fig


def fig5_recent_vs_early_bias(dws, split_date=None):
    """
    Compare the error distribution between the first 2/3 and last 1/3 of
    the evaluation period, for high-temperature dwells.
    """
    fig, axes = plt.subplots(1, 2, figsize=(13, 5))
    fig.suptitle("2CEAHVPT  |  Error Distribution: Early vs Recent Period",
                 fontsize=12, fontweight="bold")

    tstarts = dws["tstart"]
    if split_date is None:
        t_split = tstarts[int(len(tstarts) * 2 / 3)]
    else:
        t_split = split_date

    dt_split = cxc_to_datetime(t_split)[0]
    early = dws["tstart"] < t_split
    recent = ~early
    hi = dws["obs_mean_temp"] >= HIGH_TEMP_THRESH

    bins = np.linspace(-4, 4, 60)

    for ax, temp_mask, temp_label in [
        (axes[0], np.ones(len(early), bool), "All temperatures"),
        (axes[1], hi, f"High-temp dwells (≥ {HIGH_TEMP_THRESH} °C)"),
    ]:
        for mask, label, color in [
            (early & temp_mask, f"Early (before {dt_split.strftime('%Y-%m')})", "steelblue"),
            (recent & temp_mask, f"Recent (from {dt_split.strftime('%Y-%m')})", "crimson"),
        ]:
            if mask.sum() == 0:
                continue
            ax.hist(dws["err_mean"][mask], bins=bins, density=True,
                    alpha=0.5, color=color, label=f"{label}  (n={mask.sum()})")
            ax.axvline(np.mean(dws["err_mean"][mask]), color=color, lw=2.0, ls="--")

        ax.axvline(0, color="k", lw=0.8, ls=":")
        ax.set_xlabel("Mean bias per dwell (°C)")
        ax.set_ylabel("Density")
        ax.set_title(temp_label)
        ax.legend(fontsize=7.5)
        ax.grid(True, alpha=0.3)

    fig.tight_layout()
    return fig


def fig6_monthly_high_temp_p95(dws):
    """
    Monthly 95th percentile of per-dwell max_abs_error for high-temperature
    dwells only. The metric most relevant to limit exceedances.
    """
    fig, ax = plt.subplots(figsize=(12, 5))
    fig.suptitle(
        f"2CEAHVPT  |  Monthly 95th-pct Max |Error|  —  High-temp dwells (≥ {HIGH_TEMP_THRESH} °C)\n"
        "(per-dwell maximum absolute error, 95th percentile within each month)",
        fontsize=11, fontweight="bold",
    )

    hi = dws["obs_mean_temp"] >= HIGH_TEMP_THRESH
    if hi.sum() == 0:
        ax.text(0.5, 0.5, "No high-temp dwells", transform=ax.transAxes, ha="center")
        return fig

    mc, mm, ms, mn = monthly_stats(dws["dt"][hi], dws["err_max_abs"][hi])

    # Use 95th pct within each month separately
    months_dict = {}
    for dt, v in zip(dws["dt"][hi], dws["err_max_abs"][hi]):
        key = (dt.year, dt.month)
        months_dict.setdefault(key, []).append(v)

    mc2, p95 = [], []
    for (yr, mo), vals in sorted(months_dict.items()):
        mc2.append(datetime(yr, mo, 15, tzinfo=timezone.utc))
        p95.append(np.percentile(vals, 95))

    mc2 = np.array(mc2)
    p95 = np.array(p95)

    _add_recent_shade(ax, dws["dt"])
    ax.plot(mc2, p95, "o-", color="crimson", lw=2.0, ms=6,
            label="Monthly p95 of per-dwell max |error|")

    ax.axhline(1.0, color="orange", lw=1.0, ls="--", label="1.0 °C reference")
    ax.set_ylabel("95th pct max |error| (°C)")
    ax.set_xlabel("Month")
    ax.xaxis.set_major_formatter(MONTH_FMT)
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3)
    fig.autofmt_xdate(rotation=30)
    fig.tight_layout()
    return fig


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "2ceahvpt.json.gz"
    path = Path(path)

    print(f"Loading {path} ...")
    data = load_data(path)

    print("Building per-dwell table ...")
    dws = build_dwell_table(data)

    n = len(dws["tstart"])
    print(f"  {n} dwells spanning {dws['dt'][0].strftime('%Y-%m')} to {dws['dt'][-1].strftime('%Y-%m')}")
    hi = dws["obs_mean_temp"] >= HIGH_TEMP_THRESH
    print(f"  {hi.sum()} high-temp dwells (obs_mean >= {HIGH_TEMP_THRESH} degC)")

    stats = data["stats"]
    print(f"\nOverall stats: mean={stats['mean']:.4f} rms={stats['rms']:.4f} "
          f"max_abs={stats['max_abs']:.3f} degC")
    print(f"Violations: {data['violations']['count']} steps "
          f"({100*data['violations']['fraction']:.2f}%)")

    # -- Print per-month bias for high-temp dwells (text summary) ----------
    print(f"\nMonthly mean bias for high-temp dwells (obs_mean >= {HIGH_TEMP_THRESH} degC):")
    months_hi = {}
    for dt, v in zip(dws["dt"][hi], dws["err_mean"][hi]):
        key = (dt.year, dt.month)
        months_hi.setdefault(key, []).append(v)
    for (yr, mo), vals in sorted(months_hi.items()):
        print(f"  {yr}-{mo:02d}  n={len(vals):4d}  mean={np.mean(vals):+.3f}  "
              f"p95_abs={np.percentile(np.abs(vals), 95):.3f} degC")

    # -- Figures ------------------------------------------------------------
    print("\nGenerating plots ...")
    figs = [
        ("fig1_monthly_bias.png",        fig1_overall_monthly_bias(dws, stats)),
        ("fig2_error_vs_time.png",       fig2_high_temp_error_vs_time(dws)),
        ("fig3_rolling_rms.png",         fig3_rolling_rms_by_temp_regime(dws)),
        ("fig4_error_vs_temperature.png",fig4_error_vs_temperature(dws)),
        ("fig5_early_vs_recent.png",     fig5_recent_vs_early_bias(dws)),
        ("fig6_high_temp_p95.png",       fig6_monthly_high_temp_p95(dws)),
    ]
    for fname, fig in figs:
        fig.savefig(fname, dpi=150, bbox_inches="tight")
        print(f"  Saved {fname}")

    plt.show()
    print("Done.")


if __name__ == "__main__":
    main()
