#!/usr/bin/env python3
"""Argus Enforcer CLI — execute approved proposals and manage enforcement."""

from __future__ import annotations
import argparse, asyncio, json, sys

def parse_args():
    p = argparse.ArgumentParser(description="Argus Enforcer — execute approved proposals")
    s = p.add_subparsers(dest="command", required=True)

    ex = s.add_parser("execute", help="Execute an approved proposal")
    ex.add_argument("proposal_id", help="Approved proposal ID")
    ex.add_argument("--executor", default="argus-cli", help="Executor identity")
    ex.add_argument("--dry-run", action="store_true", help="Dry-run only")
    ex.add_argument("--skip-precheck", action="store_true")
    ex.add_argument("--skip-verification", action="store_true")
    ex.add_argument("--format", choices=["text", "json"], default="text")

    ls = s.add_parser("list", help="List enforcements")
    ls.add_argument("--proposal-id", help="Filter by proposal ID")
    ls.add_argument("--status", help="Filter by status")
    ls.add_argument("--limit", type=int, default=20)
    ls.add_argument("--format", choices=["text", "json"], default="text")

    get = s.add_parser("get", help="Get enforcement details")
    get.add_argument("enforcement_id", help="Enforcement ID")
    get.add_argument("--format", choices=["text", "json"], default="text")

    cfg = s.add_parser("config", help="View or update enforcer config")
    cfg.add_argument("--show", action="store_true", help="Show current config")
    cfg.add_argument("--set", nargs=2, metavar=("KEY", "VALUE"), help="Set a config value")
    cfg.add_argument("--format", choices=["text", "json"], default="text")

    return p.parse_args()

def _json(d): return json.dumps(d, indent=2, default=str)

async def cmd_execute(args):
    from app.enforcer.enforcer import EnforcerCoordinator
    from app.enforcer.models import EnforceRequest
    req = EnforceRequest(proposal_id=args.proposal_id, executed_by=args.executor, dry_run=args.dry_run, skip_precheck=args.skip_precheck, skip_verification=args.skip_verification)
    resp = await EnforcerCoordinator.enforce(req)
    if args.format == "json":
        print(_json(resp.model_dump(mode="json")))
    else:
        enf = resp.enforcement
        print(f"\n{'='*60}")
        print(f"  ENFORCEMENT RESULT  [{enf.status.value.upper()}]")
        print(f"{'='*60}")
        print(f"  ID:       {enf.id}")
        print(f"  Proposal: {enf.proposal_id}")
        print(f"  Action:   {enf.proposal_action}")
        print(f"  Target:   {enf.proposal_target}")
        print(f"  Status:   {enf.status.value}")
        print(f"  Dry-run:  {enf.dry_run}")
        if resp.precheck:
            print(f"  Precheck: {'PASSED' if resp.precheck.passed else 'FAILED'}")
            for f in resp.precheck.failures: print(f"    ! {f}")
        print(f"  Result:   {enf.execution_result or enf.error_message or 'N/A'}")
        print(f"  Message:  {resp.message}")
        if enf.pr_url: print(f"  PR URL:   {enf.pr_url}")
        if enf.duration_seconds: print(f"  Duration: {enf.duration_seconds:.1f}s")
        print()

async def cmd_list(args):
    from app.enforcer.enforcer import EnforcerCoordinator
    from app.enforcer.models import EnforcementStatus
    status = EnforcementStatus(args.status) if args.status else None
    items = await EnforcerCoordinator.list_enforcements(proposal_id=args.proposal_id, status=status, limit=args.limit)
    if args.format == "json":
        print(_json({"count": len(items), "enforcements": [e.model_dump(mode="json") for e in items]}))
    else:
        print(f"\nEnforcements ({len(items)}):")
        print(f"  {'ID':<30} {'Proposal':<22} {'Action':<16} {'Status':<14}")
        print(f"  {'-'*84}")
        for e in items:
            print(f"  {e.id[:28]:<30} {e.proposal_id[:20]:<22} {e.proposal_action[:14]:<16} {e.status.value:<14}")
        print()

async def cmd_get(args):
    from app.enforcer.enforcer import EnforcerCoordinator
    enf = await EnforcerCoordinator.get_enforcement(args.enforcement_id)
    if not enf: print(f"Enforcement '{args.enforcement_id}' not found"); return
    if args.format == "json":
        print(_json(enf.model_dump(mode="json")))
    else:
        d = enf.model_dump()
        print(f"\nEnforcement: {d['id']}")
        for k, v in d.items():
            if v is not None and v != "" and v != [] and v != {}:
                print(f"  {k}: {v}")
        print()

async def cmd_config(args):
    from app.enforcer.enforcer import EnforcerCoordinator
    from app.enforcer.models import EnforcerConfig
    if args.set:
        cfg = await EnforcerCoordinator.get_config()
        k, v = args.set
        try: v = int(v)
        except ValueError:
            try: v = float(v)
            except:
                if v.lower() in ("true","false"): v = v.lower() == "true"
        setattr(cfg, k, v)
        await EnforcerCoordinator.update_config(cfg)
        print(f"Config updated: {k} = {v}")
    cfg = await EnforcerCoordinator.get_config()
    if args.format == "json":
        print(_json(cfg.model_dump()))
    else:
        print(f"\nEnforcer Config:")
        print(f"  {'-':<40}")
        for k, v in cfg.model_dump().items():
            print(f"  {k:<30} {v}")
        print()

async def main():
    args = parse_args()
    if args.command == "execute": await cmd_execute(args)
    elif args.command == "list": await cmd_list(args)
    elif args.command == "get": await cmd_get(args)
    elif args.command == "config": await cmd_config(args)

if __name__ == "__main__":
    asyncio.run(main())
