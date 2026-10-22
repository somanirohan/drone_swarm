import argparse
import json
import sys

from . import kinds, roles, run_evals


def main(argv=None):
    ap = argparse.ArgumentParser(description="Onboard reasoning scenario suite (no LLM, offline).")
    ap.add_argument("--solver", default="greedy", choices=["greedy", "cpsat"])
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    res = run_evals(a.solver)
    if a.json:
        print(json.dumps(res, indent=1, default=str))
    else:
        for c in res["cases"]:
            p = c["plan"]
            mark = "PASS" if all(ok for _, ok in c["checks"]) else "FAIL"
            print(f"[{mark}] {c['id']}: {p['escalation'] if p else 'NONE'} sev={p['assessment']['severity'] if p else '-'} "
                  f"requests={kinds(p) if p else {}} drones={ {f'{r}/{pl}': n for (r, pl), n in roles(p).items()} if p else {} }")
            for name, ok in c["checks"]:
                if not ok:
                    print(f"       x {name}")
        for pr in res["pairs"]:
            print(f"[{'PASS' if pr['ok'] else 'FAIL'}] pair: {pr['name']}")
        print(f"checks {res['checks_passed']}/{res['checks_total']}, pairs {res['pairs_passed']}/{res['pairs_total']}, "
              f"latency ms {res['latency_ms']}")
    return 0 if res["checks_passed"] == res["checks_total"] and res["pairs_passed"] == res["pairs_total"] else 1


if __name__ == "__main__":
    sys.exit(main())
