#!/usr/bin/env python3
"""Link each machine to its game in GATBOX's library, from the plan the deck's seed was made with.

    python scripts/link_model_keys.py PLAN.json                # dry run: what would be linked
    python scripts/link_model_keys.py PLAN.json --apply

PLAN.json is the file ``gatbox-library seed --plan`` wrote on the deck and the owner edited before
``seed --apply`` (``{"games": {slug: {"key": ..., "skip": ...}}}``). A machine's ``barcode`` is its roster
slug, so ``plan.games[barcode].key`` is the game it is. This sets ``Game.model_key`` and nothing else.

* A machine with a plan entry and no key yet is linked. One that already has the same key is left alone, so a
  second run finds nothing to do.
* A machine that already has a *different* key is reported and left alone: someone chose it on the machine
  page, and a plan made earlier doesn't overrule that. ``--force`` overrides.
* Entries marked ``skip``, entries without a usable key, and machines the plan doesn't name are listed, never guessed.

The plan holds the real floor list, so it never goes in the repository (keep it outside it). This script
carries no data, and its tests use an invented plan. Run it after the migration, once; after that managers
change a link on the machine page.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def make(plan: dict, force: bool = False) -> dict:
    """The changes a plan asks for, without making them: {"link": [(game, key)], "same": n, "differs": [...], "skipped": [...],
    "unusable": [...], "unnamed": [...]}. Needs an application context."""
    from app.models import Game
    from app.services import library

    entries = plan.get("games") if isinstance(plan, dict) else None
    if not isinstance(entries, dict):
        raise TypeError("not a plan: it has no games")
    out = {"link": [], "same": 0, "differs": [], "skipped": [], "unusable": [], "unnamed": []}
    for game in Game.query.order_by(Game.name).all():
        if not game.barcode:
            continue
        entry = entries.get(game.barcode)
        if not isinstance(entry, dict):
            out["unnamed"].append(game.name)
            continue
        key = entry.get("key")
        if entry.get("skip"):
            out["skipped"].append(game.name)
        elif not isinstance(key, str) or not library.valid_key(key):
            out["unusable"].append(f"{game.name}: {key!r}")
        elif game.model_key == key:
            out["same"] += 1
        elif game.model_key and not force:
            out["differs"].append(f"{game.name}: has {game.model_key!r}, plan says {key!r}")
        else:
            out["link"].append((game, key))
    return out


def main(app=None, argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("plan", help="the plan file from `gatbox-library seed --plan`, as edited")
    ap.add_argument("--apply", action="store_true", help="make the changes (the default only shows them)")
    ap.add_argument("--force", action="store_true", help="also replace a key that was set by hand")
    args = ap.parse_args(argv)
    try:
        with open(args.plan, encoding="utf-8") as f:
            plan = json.load(f)
    except (OSError, ValueError) as e:
        print(f"can't read the plan: {e}", file=sys.stderr)
        return 2
    if app is None:
        from app import create_app

        app = create_app()
    with app.app_context():
        from app.extensions import db

        try:
            res = make(plan, args.force)
        except TypeError as e:
            print(e, file=sys.stderr)
            return 2
        for game, key in res["link"]:
            print(f"  {'link' if args.apply else 'would link'}  {game.name}  ->  {key}")
            if args.apply:
                game.model_key = key
        if args.apply:
            db.session.commit()
        for label in ("differs", "unusable", "skipped", "unnamed"):
            for line in res[label]:
                print(f"  {label:9s} {line}")
        verb = "linked" if args.apply else "would link"
        print(f"{verb} {len(res['link'])}; already linked {res['same']}; left alone {len(res['differs'])} "
              f"(different key), {len(res['unusable'])} (no usable key), {len(res['skipped'])} (skipped), "
              f"{len(res['unnamed'])} (not in the plan)" + ("" if args.apply else "; nothing changed, add --apply"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
