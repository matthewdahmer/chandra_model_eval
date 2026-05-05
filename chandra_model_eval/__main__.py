"""Command-line interface for chandra_model_eval.

Usage
-----
    chandra-model-eval tstart tstop outdir models_root [options]
    chandra-model-eval --trailing-days 365 outdir models_root [options]
"""

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone

from .models import MODELS, run_all_models


def _parse_limit_override(s):
    """Parse 'MSID=VALUE' into (msid, float(value))."""
    parts = s.split('=', 1)
    if len(parts) != 2:
        raise argparse.ArgumentTypeError(f"expected MSID=VALUE, got {s!r}")
    msid, val = parts
    try:
        return msid.strip(), float(val)
    except ValueError:
        raise argparse.ArgumentTypeError(f"limit value must be a number, got {val!r}")


def main():
    parser = argparse.ArgumentParser(
        prog='chandra-model-eval',
        description=(
            'Evaluate Chandra xija thermal models over a time range and write '
            '.json.gz result files. Provide tstart and tstop, or use '
            '--trailing-days to specify a rolling window ending at the current time.'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            'Examples:\n'
            '  chandra-model-eval 2025:001 2026:001 /data/out ~/AXAFLIB/chandra_models\n'
            '  chandra-model-eval --trailing-days 365 /data/out ~/AXAFLIB/chandra_models\n'
            '      --limit-override pm2thv1t=227.5\n'
            '  chandra-model-eval --trailing-days 30 /data/out ~/AXAFLIB/chandra_models\n'
            '      --model 1dpamzt\n'
            '  chandra-model-eval --trailing-days 30 /data/out ~/AXAFLIB/chandra_models\n'
            '      --models 1dpamzt aacccdpt --log-file /data/out/run.log'
        ),
    )

    # Time range — either explicit tstart/tstop or --trailing-days
    time_group = parser.add_argument_group('time range (pick one)')
    time_group.add_argument(
        'tstart', nargs='?',
        help='Start time in any CXC-accepted format (e.g. 2025:001 or 2025-01-01T00:00:00)',
    )
    time_group.add_argument(
        'tstop', nargs='?',
        help='Stop time (same format as tstart)',
    )
    time_group.add_argument(
        '--trailing-days', type=int, metavar='N',
        help='Use a rolling window of N days ending at UTC now (or --end-date if given)',
    )
    time_group.add_argument(
        '--end-date', metavar='DATE',
        help=(
            'End date for --trailing-days window (e.g. 2025:180 or 2025-06-29). '
            'Defaults to UTC now when omitted.'
        ),
    )

    parser.add_argument('outdir', help='Directory to write {msid}.json.gz output files')
    parser.add_argument('models_root', help='Root path of the chandra_models repository checkout')

    parser.add_argument(
        '--limit-override', metavar='MSID=VALUE', action='append',
        type=_parse_limit_override, default=[],
        dest='limit_overrides',
        help=(
            'Override the planning limit for a model (e.g. pm2thv1t=227.5). '
            'Required for pm2thv1t, which has no limit in its spec. '
            'May be repeated.'
        ),
    )
    parser.add_argument(
        '--model', metavar='MSID',
        choices=sorted(MODELS),
        help=(
            'Run a single model by MSID. '
            f'Choices: {", ".join(sorted(MODELS))}'
        ),
    )
    parser.add_argument(
        '--spec', metavar='PATH',
        help=(
            'Path to a model spec JSON file. '
            'Overrides the default spec for the model given by --model. '
            'Only valid with --model.'
        ),
    )
    parser.add_argument(
        '--models', metavar='MSID', nargs='+',
        choices=sorted(MODELS),
        help=(
            'Run only these models (default: all 14). '
            f'Choices: {", ".join(sorted(MODELS))}'
        ),
    )
    parser.add_argument(
        '--log-level', default='INFO',
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
        help='Logging verbosity (default: INFO)',
    )
    parser.add_argument(
        '--log-file', metavar='PATH',
        help='Write log output to this file instead of stderr',
    )

    args = parser.parse_args()

    # --- Resolve model selection ---
    if args.model and args.models:
        parser.error('--model and --models are mutually exclusive')
    if args.spec and not args.model:
        parser.error('--spec requires --model')
    if args.model:
        models_to_run = [args.model]
    else:
        models_to_run = args.models  # None means all 14

    spec_overrides = {args.model: args.spec} if args.spec else {}

    # --- Resolve time range ---
    if args.trailing_days is not None:
        if args.end_date is not None:
            from cxotime import CxoTime
            end = CxoTime(args.end_date).datetime
        else:
            end = datetime.now(timezone.utc)
        tstop = end.strftime('%Y:%j')
        tstart = (end - timedelta(days=args.trailing_days)).strftime('%Y:%j')
    elif args.tstart and args.tstop:
        if args.end_date is not None:
            parser.error('--end-date requires --trailing-days')
        tstart, tstop = args.tstart, args.tstop
    else:
        parser.error(
            'provide tstart and tstop positional arguments, or --trailing-days N'
        )

    # --- Configure logging ---
    log_kwargs = {
        'level': getattr(logging, args.log_level),
        'format': '%(asctime)s %(levelname)s %(name)s: %(message)s',
        'datefmt': '%Y-%m-%dT%H:%M:%S',
    }
    if args.log_file:
        log_kwargs['filename'] = args.log_file
    logging.basicConfig(**log_kwargs)

    # --- Run ---
    limit_overrides = dict(args.limit_overrides)

    result = run_all_models(
        tstart=tstart,
        tstop=tstop,
        outdir=args.outdir,
        models_root=args.models_root,
        limit_overrides=limit_overrides,
        models=models_to_run,
        spec_overrides=spec_overrides,
    )

    n_ok = len(result['succeeded'])
    n_fail = len(result['failed'])
    print(f'{n_ok} succeeded, {n_fail} failed', file=sys.stderr)

    if result['failed']:
        for msid, msg in result['failed'].items():
            print(f'  FAILED {msid}: {msg}', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
