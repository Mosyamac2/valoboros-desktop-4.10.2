"""CLI entrypoint for the Kaggle harvester.

Subcommands:
  probe                       smoke-test Kaggle credentials
  discover --domains tabular,nlp
  inspect <competition-slug>
  verify <bundle.zip>
  run --count N [--dry-run] [--allow-list FILE] [--resume]
"""

from __future__ import annotations

import argparse
import logging
import pathlib
import random
import shutil
import sys
import tempfile
from typing import Optional

from . import state as state_mod
from .allow_list import AllowList
from .auth import KaggleAuth, KaggleAuthError, load_credentials, smoke_probe
from .bundle_assembler import assemble, summarize
from .data_subsampler import extract_archive, maybe_subsample
from .discovery import (
    CompetitionCandidate,
    _to_candidate,
    iter_candidates,
    probe_data_access,
)
from .kaggle_http import KaggleClient, KaggleHttpError
from .kernel_picker import pick_moderate_kernel
from .notebook_size_guard import maybe_strip_outputs

DEFAULT_INBOX = pathlib.Path.home() / "Ouroboros" / "data" / "ml-models-to-validate"
DEFAULT_DOMAINS = ("tabular", "nlp")


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )


def _ensure_disk_headroom(min_gb: float = 5.0) -> None:
    usage = shutil.disk_usage(pathlib.Path.home())
    free_gb = usage.free / (1024 ** 3)
    if free_gb < min_gb:
        raise SystemExit(
            f"Refusing to run: only {free_gb:.1f} GB free in {pathlib.Path.home()}, "
            f"need ≥ {min_gb:.1f} GB. Free up disk or use --inbox on a larger volume."
        )


def cmd_probe(args: argparse.Namespace) -> int:
    try:
        auth = load_credentials()
    except KaggleAuthError as e:
        print(f"AUTH MISSING: {e}", file=sys.stderr)
        return 2
    ok, msg = smoke_probe(auth)
    print(msg)
    return 0 if ok else 3


def cmd_discover(args: argparse.Namespace) -> int:
    auth = load_credentials()
    client = KaggleClient(auth=auth)
    domains = frozenset(args.domains.split(","))
    state = state_mod.load()
    seen = frozenset(state.seen())
    candidates = list(iter_candidates(
        client, domains=domains, seen_slugs=seen,
        closed_only=not args.include_open,
    ))
    print(f"Found {len(candidates)} candidate competitions:")
    for c in candidates[: args.limit]:
        print(f"  [{c.inferred_domain:>7}] {c.slug:30s} {c.title[:60]}")
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    auth = load_credentials()
    client = KaggleClient(auth=auth)
    # Fetch a single competition's metadata via list-with-search.
    matches = client.list_competitions(search=args.slug, page_size=20)
    raw = next(
        (m for m in matches
         if m.get("urlNullable", "").rstrip("/").endswith("/" + args.slug)),
        None,
    )
    if raw is None:
        print(f"Competition {args.slug!r} not found in Kaggle's listing.")
        return 4
    cand = _to_candidate(raw)
    if cand is None:
        print(f"Could not parse competition {args.slug!r}.")
        return 4
    print(f"Competition: {cand.title}")
    print(f"  slug:      {cand.slug}")
    print(f"  domain:    {cand.inferred_domain}")
    print(f"  category:  {cand.category}")
    print(f"  metric:    {cand.evaluation_metric}")
    print(f"  deadline:  {cand.deadline_iso}")
    print(f"  closed:    {cand.is_closed}")
    print(f"  url:       {cand.url}")
    accessible, reason = probe_data_access(client, cand)
    print(f"  data:      {'accessible' if accessible else 'INACCESSIBLE: ' + reason}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    path = pathlib.Path(args.bundle).expanduser().resolve()
    print(summarize(path))
    return 0


def _process_one(
    cand: CompetitionCandidate,
    client: KaggleClient,
    state: state_mod.HarvesterState,
    rng: random.Random,
    *,
    inbox: pathlib.Path,
    dry_run: bool,
) -> tuple[bool, str]:
    """Process a single competition. Returns ``(success, message)``."""
    accessible, reason = probe_data_access(client, cand)
    if not accessible:
        state.record_skip(cand.slug, reason)
        return False, f"skip {cand.slug}: {reason}"

    with tempfile.TemporaryDirectory(prefix=f"kaggle-harvest-{cand.slug}-") as tmp:
        workdir = pathlib.Path(tmp)
        kernel_dir = workdir / "kernel"
        kernel, kernel_reason = pick_moderate_kernel(client, cand.slug, kernel_dir, rng=rng)
        if kernel is None:
            state.record_skip(cand.slug, f"kernel_pick_failed:{kernel_reason}")
            return False, f"skip {cand.slug}: kernel_pick_failed ({kernel_reason})"

        data_archive_dir = workdir / "raw"
        try:
            archive = client.download_competition(cand.slug, data_archive_dir)
        except KaggleHttpError as e:
            state.record_skip(cand.slug, f"data_download_http_{e.status}")
            return False, f"skip {cand.slug}: data_download_http_{e.status}"

        extracted = workdir / "extracted"
        extract_archive(archive, extracted)
        subsample = maybe_subsample(extracted)
        if subsample.reason == "minority_floor_protection":
            state.record_skip(cand.slug, "minority_floor_protection")
            return False, f"skip {cand.slug}: minority_floor_protection ({subsample.note})"

        _stripped, nb_note = maybe_strip_outputs(kernel.source_path)
        bundle = assemble(
            cand=cand, kernel=kernel,
            extracted_data_dir=extracted, subsample=subsample,
            notebook_note=nb_note,
            inbox_dir=inbox, workdir=workdir,
            dry_run=dry_run,
        )

        # When dry-run, copy the ZIP out before workdir is wiped.
        if dry_run:
            keep_dir = pathlib.Path.home() / ".kaggle_harvester" / "dry-run-bundles"
            keep_dir.mkdir(parents=True, exist_ok=True)
            target = keep_dir / bundle.zip_path.name
            shutil.copy2(bundle.zip_path, target)
            state.record_harvest(cand.slug, target, kernel.ref)
            return True, (
                f"DRY-RUN built {cand.slug}: {target} "
                f"({bundle.bytes_total / 1024:.1f} KB)"
            )

        state.record_harvest(cand.slug, bundle.zip_path, kernel.ref)
        return True, (
            f"harvested {cand.slug} → {bundle.zip_path.name} "
            f"({bundle.bytes_total / 1024:.1f} KB)"
        )


def cmd_run(args: argparse.Namespace) -> int:
    _ensure_disk_headroom()
    auth = load_credentials()
    client = KaggleClient(auth=auth)
    state = state_mod.load() if args.resume else state_mod.HarvesterState()
    rng = random.Random(args.seed if args.seed is not None else random.SystemRandom().randint(0, 2 ** 30))
    inbox = pathlib.Path(args.inbox).expanduser().resolve()
    domains = frozenset(args.domains.split(","))
    target = args.count

    needed = max(0, target - len(state.harvested))
    if needed == 0:
        print(f"State already has {len(state.harvested)} bundles; nothing to do.")
        return 0

    # ---- Tier 1: walk Kaggle's listing ---------------------------------
    closed_only = not args.include_open
    print(f"Tier 1: walking Kaggle listing for domains={sorted(domains)}, "
          f"target={needed}, closed_only={closed_only}")
    tier1_success = 0
    for cand in iter_candidates(client, domains=domains,
                                seen_slugs=frozenset(state.seen()),
                                closed_only=closed_only):
        if tier1_success >= needed:
            break
        ok, msg = _process_one(cand, client, state, rng,
                               inbox=inbox, dry_run=args.dry_run)
        print(msg)
        if ok:
            tier1_success += 1
        state_mod.save(state)
    # ---- Tier 2: allow-list --------------------------------------------
    remaining = needed - tier1_success
    if remaining > 0 and args.allow_list:
        allow = AllowList.load(pathlib.Path(args.allow_list).expanduser())
        if not allow:
            print(f"Tier 2 requested but allow-list file is empty: {args.allow_list}")
        else:
            print(f"Tier 2: walking user allow-list of {len(allow.slugs)} slugs, "
                  f"need {remaining} more")
            state.tier = 2
            for slug in allow.slugs:
                if remaining <= 0:
                    break
                if slug in state.seen():
                    continue
                matches = client.list_competitions(search=slug, page_size=10)
                raw = next(
                    (m for m in matches
                     if m.get("urlNullable", "").rstrip("/").endswith("/" + slug)),
                    None,
                )
                cand = _to_candidate(raw) if raw else None
                if cand is None:
                    state.record_skip(slug, "allow_list_slug_not_resolved")
                    continue
                ok, msg = _process_one(cand, client, state, rng,
                                       inbox=inbox, dry_run=args.dry_run)
                print(msg)
                if ok:
                    remaining -= 1
                state_mod.save(state)

    total = len(state.harvested)
    final_msg = (
        f"\nHarvester finished: {total} total bundles in state; "
        f"{tier1_success} freshly harvested via tier 1."
    )
    if args.dry_run:
        final_msg += f"\nDry-run bundles kept at: ~/.kaggle_harvester/dry-run-bundles/"
    else:
        final_msg += f"\nLive bundles delivered to: {inbox}"
    print(final_msg)
    return 0 if total >= target else 5


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m scripts.kaggle_harvester",
        description="Pull Kaggle competition + kernel artifacts into Valoboros's inbox.",
    )
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("probe", help="Smoke-test Kaggle credentials")
    sp.set_defaults(func=cmd_probe)

    sp = sub.add_parser("discover", help="List candidate competitions")
    sp.add_argument("--domains", default=",".join(DEFAULT_DOMAINS))
    sp.add_argument("--limit", type=int, default=30)
    sp.add_argument("--include-open", action="store_true",
                    help="Also list competitions whose deadline hasn't passed")
    sp.set_defaults(func=cmd_discover)

    sp = sub.add_parser("inspect", help="Show what the harvester would do for one competition")
    sp.add_argument("slug")
    sp.set_defaults(func=cmd_inspect)

    sp = sub.add_parser("verify", help="Read a built bundle ZIP and print a summary")
    sp.add_argument("bundle")
    sp.set_defaults(func=cmd_verify)

    sp = sub.add_parser("run", help="Run the full harvester")
    sp.add_argument("--count", type=int, default=5)
    sp.add_argument("--domains", default=",".join(DEFAULT_DOMAINS))
    sp.add_argument("--inbox", default=str(DEFAULT_INBOX))
    sp.add_argument("--dry-run", action="store_true",
                    help="Build bundles but keep them in ~/.kaggle_harvester/dry-run-bundles/")
    sp.add_argument("--resume", action="store_true",
                    help="Continue from state.json instead of starting fresh")
    sp.add_argument("--include-open", action="store_true",
                    help="Also harvest competitions whose deadline has not passed. "
                         "Most Kaggle competitions are perpetual or have far-future "
                         "deadlines, so the closed-only default often yields 0 candidates.")
    sp.add_argument("--allow-list", default="",
                    help="Tier-2 fallback: file with pre-accepted competition slugs (one per line)")
    sp.add_argument("--seed", type=int, default=None)
    sp.set_defaults(func=cmd_run)
    return p


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
