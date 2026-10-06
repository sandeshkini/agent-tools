"""agent-tools setup: bring this machine to its profile.

    setup.sh --check                 what's in place vs the profile; changes nothing
    setup.sh [--apply]               apply what --check says would change (asks first on a terminal)
    setup.sh --only a,b [--check]    just these components
    setup.sh --rollback <component>  undo the last apply of a component
    setup.sh --list                  the components

Profile: --profile <file>, --profile-from <repo or folder> (picks the profile whose [match] fits this
host), else $AGENT_SETUP_PROFILE, else ~/.config/agent-setup/profile.toml. See setup/README.md.

Exit status: 0 everything in place (or applied and verified), 3 something still to change or needs a
person, 1 an error or a failed apply.
"""
import argparse
import sys
from pathlib import Path

from . import host, notify, profile
from .components import ALL, BY_NAME
from .core import CHANGE, ERROR, GAP, LABELS, OK, SKIP, Context, Plan, take_snapshot

REPO = Path(__file__).resolve().parents[2]
MARK = {OK: ("✓", "32"), CHANGE: ("•", "33"), GAP: ("!", "31"), SKIP: ("-", "90"), ERROR: ("x", "31")}


def parse(argv):
    ap = argparse.ArgumentParser(prog="setup.sh", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="report only, change nothing")
    mode.add_argument("--apply", action="store_true", help="apply (the default without --check)")
    mode.add_argument("--rollback", metavar="COMPONENT", help="undo the last apply of COMPONENT")
    mode.add_argument("--list", action="store_true", help="list components")
    ap.add_argument("--only", metavar="A,B", help="only these components")
    ap.add_argument("--profile", metavar="FILE", help="profile to use")
    ap.add_argument("--profile-from", metavar="REPO", help="pick this host's profile from a repo/folder (e.g. my-infra)")
    ap.add_argument("--yes", "-y", action="store_true", help="don't ask; also allows components that restart cptr")
    ap.add_argument("--notify", action="store_true", help="also push the --check result via ntfy")
    ap.add_argument("--verbose", "-v", action="store_true", help="print every action and note")
    return ap.parse_args(argv)


class Out:
    def __init__(self, ctx, color):
        self.ctx, self.color = ctx, color

    def mark(self, status):
        sym, code = MARK[status]
        return f"\033[{code}m{sym}\033[0m" if self.color else sym

    def plan(self, name, plan, verbose):
        self.ctx.say(f"  {self.mark(plan.status)} {name:17} {LABELS[plan.status]}: {plan.summary}")
        detail = plan.status in (CHANGE, GAP, ERROR) or verbose
        for a in plan.actions if detail else []:
            self.ctx.say(f"      - {a}")
        for h in plan.human if detail else []:
            self.ctx.say(f"      ! you: {h}")
        for n in plan.notes:
            self.ctx.say(f"      note: {n}")


def load_profile(args, ctx_probe):
    if args.profile_from:
        folder = profile.find_repo(args.profile_from, start=REPO)
        path, how = profile.pick_for_host(folder, host.identity(ctx_probe)), f"--profile-from {args.profile_from}"
    else:
        path, how = profile.locate(args.profile)
    if not path:
        return None, how
    return profile.load(path), how


def select(prof, only):
    comps = prof.get("components") or {}
    enabled = [c for c in ALL if isinstance(comps.get(c.name), dict) and comps[c.name].get("enabled", True)]
    if only:
        names = [n.strip() for n in only.split(",") if n.strip()]
        bad = [n for n in names if n not in BY_NAME]
        if bad:
            raise SystemExit(f"setup: unknown component(s) {', '.join(bad)}; see --list")
        enabled = [c for c in ALL if c.name in names]
    return [(c, comps.get(c.name) or {}) for c in enabled]


def evaluate(comp):
    """detect + plan, never raising; applies the profile's check_only."""
    na = comp.not_applicable()
    if na:
        return Plan(SKIP, na)
    comp.ctx.fresh()
    try:
        plan = comp.plan(comp.detect())
    except Exception as e:                      # a broken detector must not stop the others
        comp.ctx.log(f"{comp.name}: detection failed: {e!r}")
        return Plan(ERROR, f"detection failed: {type(e).__name__}: {e}")
    if plan.status == CHANGE and comp.cfg.get("check_only"):
        plan = Plan(GAP, plan.summary, human=["check_only in the profile: do it by hand"] + plan.actions, notes=plan.notes)
    return plan


def cmd_list(prof):
    comps = (prof or {}).get("components") or {}
    print("components (in run order):")
    for c in ALL:
        on = ""
        if prof is not None:
            cfg = comps.get(c.name)
            on = "  [on]" if isinstance(cfg, dict) and cfg.get("enabled", True) else "  [off]"
        print(f"  {c.name:17} {'/'.join({'darwin': 'macOS', 'linux': 'Linux'}[p] for p in c.platforms):12}{on}\n      {c.description}")
    return 0


def main(argv=None):
    args = parse(sys.argv[1:] if argv is None else argv)
    probe_ctx = Context(REPO, {})
    try:
        prof, how = load_profile(args, probe_ctx)
    except profile.ProfileError as e:
        print(f"setup: {e}", file=sys.stderr)
        return 1
    if args.list:
        return cmd_list(prof)
    if prof is None:
        print(f"setup: no profile ({how}).\n  Use --profile <file>, --profile-from <repo>, or copy "
              f"{REPO / 'setup/profiles/example.toml'} to ~/.config/agent-setup/profile.toml and edit it.", file=sys.stderr)
        return 1
    ctx = Context(REPO, prof, assume_yes=args.yes, verbose=args.verbose)
    out = Out(ctx, sys.stdout.isatty())
    machine = ctx.section("machine").get("name") or Path(prof["_path"]).stem
    selected = select(prof, args.only)
    comps = [cls(ctx, cfg) for cls, cfg in selected]

    if args.rollback:
        return rollback(ctx, args.rollback, dict(selected).get(BY_NAME.get(args.rollback)), machine)

    check = args.check
    ctx.log(f"==== setup {'--check' if check else '--apply'} on {machine} (profile {prof['_path']})")
    ctx.say(f"agent-tools setup {'--check (changes nothing)' if check else '(apply)'}: {machine} ({ctx.os_label})")
    ctx.say(f"  profile: {prof['_path'].replace(str(ctx.home), '~')}  [{how}]")
    ctx.say(f"  log:     {str(ctx.log_path).replace(str(ctx.home), '~')}\n")

    plans = {}
    for comp in comps:
        plans[comp.name] = evaluate(comp)
        out.plan(comp.name, plans[comp.name], args.verbose)
    counts = {s: sum(1 for p in plans.values() if p.status == s) for s in MARK}
    summary = (f"{counts[OK]} in place, {counts[CHANGE]} to change, {counts[GAP]} need a person, "
               f"{counts[SKIP]} skipped" + (f", {counts[ERROR]} errors" if counts[ERROR] else ""))
    ctx.say(f"\n{'summary' if check else 'plan'}: {summary}")
    if check:
        if args.notify:
            notify.send(ctx, f"setup --check on {machine}", summary + "\n" + digest(plans))
        return 1 if counts[ERROR] else (3 if counts[CHANGE] or counts[GAP] else 0)
    return apply_all(ctx, out, comps, plans, machine, args)


def digest(plans):
    return "\n".join(f"{n}: {LABELS[p.status]}: {p.summary}" for n, p in plans.items() if p.status != SKIP)


def apply_all(ctx, out, comps, plans, machine, args):
    todo = [c for c in comps if plans[c.name].status == CHANGE]
    if not todo:
        ctx.say("nothing to apply")
        return 3 if any(p.status in (GAP, ERROR) for p in plans.values()) else 0
    if ctx.interactive and not args.yes:
        ans = input(f"\napply {len(todo)} change(s) ({', '.join(c.name for c in todo)})? [y/N] ").strip().lower()
        if ans not in ("y", "yes"):
            ctx.say("nothing changed")
            return 3
    results = {}
    for comp in comps:                           # every enabled component, in order: earlier ones can
        plan = evaluate(comp)                    # create work for later ones (install, then wrap)
        if plan.status != CHANGE:
            if plan.status != plans[comp.name].status:
                out.plan(comp.name, plan, args.verbose)
            continue
        if comp.restarts_cptr and not (ctx.interactive or args.yes):
            results[comp.name] = (GAP, "restarts cptr and needs a person at the Mac: run "
                                       f"`setup.sh --only {comp.name}` from Terminal there")
            ctx.say(f"\n  {out.mark(GAP)} {comp.name}: {results[comp.name][1]}")
            continue
        ctx.say(f"\n== {comp.name}: {plan.summary}")
        snap = take_snapshot(ctx, comp.name, comp.owned_files())
        try:
            applied = comp.apply(plan)
        except Exception as e:
            ctx.log(f"{comp.name}: apply raised {e!r}")
            ctx.say(f"      apply failed: {type(e).__name__}: {e}")
            applied = False
        ctx.fresh()
        good, why = comp.verify() if applied else (False, "apply failed")
        if good:
            results[comp.name] = (OK, why)
            ctx.say(f"  {out.mark(OK)} {comp.name}: done, verified: {why}")
            continue
        msg = f"FAILED ({why})"
        if snap is not None:
            ctx.say(f"  {out.mark(ERROR)} {comp.name}: {msg}; rolling back")
            msg += "; rolled back" if comp.rollback() is not False else "; ROLLBACK FAILED, needs a person"
        results[comp.name] = (ERROR, msg)
        ctx.say(f"  {out.mark(ERROR)} {comp.name}: {msg}")
    failed = [n for n, (s, _) in results.items() if s == ERROR]
    lines = [f"{n}: {'OK' if s == OK else ('needs a person' if s == GAP else 'FAILED')} - {m}" for n, (s, m) in results.items()]
    ctx.say("\nresult:\n  " + "\n  ".join(lines))
    notify.send(ctx, f"setup on {machine}" + (" FAILED" if failed else ""), "\n".join(lines), 4 if failed else 3)
    if failed:
        return 1
    return 3 if any(s == GAP for s, _ in results.values()) else 0


def rollback(ctx, name, cfg, machine):
    cls = BY_NAME.get(name)
    if not cls:
        print(f"setup: unknown component {name!r}; see --list", file=sys.stderr)
        return 1
    comp = cls(ctx, cfg or {})
    na = comp.not_applicable()
    if na:
        ctx.say(f"{name}: {na}")
        return 0
    ctx.log(f"==== setup --rollback {name} on {machine}")
    ctx.say(f"rolling back {name}")
    good = comp.rollback()
    if good is None:
        return 0
    ctx.fresh()
    plan = evaluate(comp)
    ctx.say(f"{name}: rollback {'done' if good else 'FAILED'}; now: {LABELS[plan.status]}: {plan.summary}")
    notify.send(ctx, f"setup on {machine}", f"rollback {name}: {'done' if good else 'FAILED'}; now {plan.summary}",
                3 if good else 4)
    return 0 if good else 1
