# Status and look-and-feel audit

Written 2026-10-11 by the lead. "Verified by lead" means I ran or looked at it myself; "agent-reported" means a sub-agent's report that I have not re-checked. Nothing below has been run against a real DisruptionOps deployment.

## 1. Everything you asked for, and where it stands

| # | Your request | Status | Evidence / where |
|---|---|---|---|
| 1 | Explain how the system works; why it feels rigged | Done | Explanation in chat; root causes: fixed survivors, perfect sensor, pre-sorted Jev menu |
| 2 | Research + YouTube links + production architecture doc | Done | `legacy/pybullet/docs/PRODUCTION-ARCHITECTURE.md` |
| 3 | Push, proper env, a script that makes a link for a friend | Done | `scripts/share-with-friend.ps1` (verified by lead: public link + WS worked), `scripts/setup-ops.ps1`, `.env.example`. Pushed once (`71b80c6`); **everything after that is local, not pushed** |
| 4 | Smaller drones, black routes | Done | Later redesigned: mission routes dark solid, return dashed, sweeps on demand |
| 5 | Use DisruptionOps data; dock scenario launching 2–3 drones; fix half-broken UI | Done | `dock_fire` scenario (cap 3), real PC-* units from migration 029; dead tabs fixed (verified by lead) |
| 6 | Fleet 5/10/20/25/40/50, path + coverage highlight, SOS clouds, ambulance-first vs drone-first, exact zoom, no popping, logic plan, folder layout, deliverables, migration interpretation | Done | Fleet selector (verified by lead over WS), zoom-to-cursor + double-click fly-to, signals, footprints, `docs/PLAN.md`, `docs/CAPABILITIES.md`, `docs/integration/disruptionops-migrations.md`, folder reorganised |
| 7 | Small people on roofs; add a random issue / issue on the map and the system responds by itself | Done | People figures; "Random issue" + "Add issue on map" (agent-reported, random issue verified by lead through the engine) |
| 8 | Swarm is an agency; DisruptionOps is the orchestrator | Done | `agency_request` accept/decline with reason, ETA, plan; status reports; scorecard |
| 9 | Algorithms explained; WebSocket doc for a friend | Done | `docs/ALGORITHMS.md`, `docs/implementation/FRIEND-WEBSOCKET-HANDOFF.md` |
| 10 | "Chose 5 drones, got 50" | Fixed | Cause: an old server process; verified by lead (fleet 5 → 5 drones) |
| 11 | Issue count jumping 6/5/2 | Fixed | One "N total · M open · K resolved" line everywhere |
| 12 | A fired EMS ambulance must show on the map and in the friend doc | Done | Unit labels + road routes; handoff doc section A.4 with a real captured example |
| 13 | Goal-directed search (zones, boustrophedon, probability map, auctions) | Done | `swarm_ops/planning.py`, engine search; zigzag bug fixed in `3182216` (overview verified by lead) |
| 14 | PCMC scenario from migrations; command center ↔ agents; "is it agentic, where is graph.py"; all objects incl. cars; weird drone paths | Done | `assets/operations/pcmc-region.json`, `pccoe_pawana_night`, `swarm_ops/command_center.py`, `docs/SYSTEM-LOGIC-MAP.md`, `docs/AGENTIC-ASSESSMENT.md`, traffic, any-angle planner |
| 15 | Intelligent decisions, not hard-coded, not LLM | Done | `swarm_ops/reasoning/` (belief fusion, taxonomy-driven needs, optimiser, hysteresis), ~2 ms/decision; no network calls |
| 16 | Data really from DisruptionOps; deployment-ready repo | Partly | Taxonomy + catalog generated from migrations 001–038; live API client (tested only against a stub). Docker/Render/CI files exist; **image never built** (no Docker here). Final deploy pass deferred until you test |
| 17 | Smart, correct algorithm names | Done | `docs/ALGORITHM-GLOSSARY.md` (honest implemented/partial/planned) |
| 18 | Great continuous logging | Done | `swarm_ops/telemetry/`, `.cache/logs`, `.cache/runs/*`, `/ops/runs`; charts PNG blocked by a numpy crash in the conda env |
| 19 | Example script that plays DisruptionOps | Done | `examples/disruptionops_simulator.py` (verified by lead: 3/3 accepted, real dispatches, why-texts) |
| 20 | Right-side live ops log; Apple-style UI from the real DESIGN.md | Done | `docs/design/apple-DESIGN.md` (byte-identical to the GitHub file, verified), `web/ops/*` |
| 21 | Routes look rigged (your screenshots) | Fixed | Sweeps are parallel lanes, hidden by default; mission line straight to target (verified by lead) |
| 22 | Test-case audit; WebSocket fully controllable by DisruptionOps (it assigns drone + ambulance, we execute and show) | Done | Commands `orchestrate_incident`, `assign`, `unassign`, `recall_drone`, `update_incident`, `cancel_incident`, `close_incident`, `control`; G1–G21 matrix passes (`127ef56`, caveats in `docs/TEST-AUDIT.md`) |
| 23 | Fetch and integrate new migrations (up to 038) | Done | 037 held reports, 038 agency replies; our patch renumbered to 039/040; swarm reply mapped to `reply_kind` |
| 24 | Technical doc for your friend; deploy and give a link | Waiting on you | You asked to test first |

### Open items I know about
- Not pushed since `71b80c6`; push needs your OK (remote is `tusharsingh-spring/drone_swarm`).
- Never run against a real DisruptionOps; the DisruptionOps-side patch needs its 3 edits applied there.
- Docker image not built; `render.yaml` not parsed.
- Full suite run by lead on 2026-10-11: **211 tests, all pass** (213 s). Browser test and acceptance scripts not re-run in that pass.
- The swarm declines new requests at 12 open incidents (deliberate cap; easy to raise).
- Planned, not implemented: CBBA, behaviour trees/utility AI, START triage, learned risk model, PX4/Gazebo.
- `docs/implementation/validation.md` still quotes old test counts.

## 2. Look-and-feel audit (from screenshots I looked at: district overview, selected drone, PCMC region with log)

What already works:
- Clean light canvas, one blue, pill controls, translucent panels: reads as Apple-like, not a generic dashboard.
- The live log is readable: time, glyph, one line, actor/kind subline, with `why` for decisions.
- The map is legible at overview; mission line is a single straight stroke; sweep clutter is gone.
- Performance is fine (agent-reported 58–144 fps on this laptop's GPU).

What still feels unfinished, in priority order:
1. **Focus camera goes far too close.** Selecting a drone fills the screen with the drone; a fixed comfortable distance (and a smooth ease) is needed.
2. **Toolbar overload.** 13 buttons in one map toolbar (Overview…Roads). Group into a camera segmented control, a "Layers" popover (Routes, Search map, Coverage, Wards, Traffic, Labels, Buildings, Roads) and a Log toggle.
3. **Left panel is one long scroll.** Mission tab mixes scenario, fleet, rescue issue, scorecard and a second "Latest activity" list that duplicates the live log. Remove the duplicate list; move the scorecard to its own card or tab.
4. **Tab row is cramped.** "Command" pill touches the panel edge; use a segmented control or fewer tabs (Mission · Incidents · Fleet · Response, with Command in the right panel).
5. **Label clutter on the region map.** Long unit labels ("PC-POL-04 · Pimpri Chinchwad Polic…") overlap; show short ids at overview and full names on hover/selection.
6. **Live log width.** About a quarter of the screen; default narrower with wrapping, and let rows expand on click instead of truncating with an ellipsis.
7. **Hover drop line looks diagonal** at oblique angles; draw it as a true vertical with a small ground ring.
8. **Closed-road barriers float over flood water** (agent-noted).
9. **Region buildings are procedural** (no footprints in the data), so streets look repetitive up close.
10. **Not checked by me:** dark mode, mobile width, people groups on roofs reading as N figures, the orchestrated badge on a real orchestrated incident.

## 3. Suggested order for the next pass
1. Finish the orchestrated test matrix and run the full suite (in progress).
2. UI polish items 1–7 above (one frontend pass).
3. You test locally; then the friend technical doc, push, Docker build and deploy.
