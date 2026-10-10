# A 100+ drone disaster-response architecture

Research and repository review, 10 October 2026. **Proposal for discussion; no flight or application code has been changed.**

Start with [the architecture study](architecture-study.md). It explains how coordinated drones work, what your repositories already do, and what changes for a real fleet. The example is a **hypothetical flood near Morigaon, Assam**, not a reconstruction of a real deployment.

For the specific question of twenty drones dividing unassigned search work without a single coordinator, read [the decentralized allocation study](20-drone-decentralized-allocation.md) and [its peer-allocation diagram](06-decentralized-allocation.svg). This expands the earlier field-coordinator proposal with an onboard peer-auction option, explicit partition limitations and a staged simulator implementation plan.

For the real-time mechanism without an LLM, start with [04-realtime-mechanism.svg](04-realtime-mechanism.svg) and its [editable DOT source](04-realtime-mechanism.dot). This focuses on sensor fusion, trajectory exchange, local planning, feedback control and event-driven task allocation independently of either repository.

The newer [six-page draw.io diagram](drawio/50-drone-realtime-system.drawio) expands all five mechanisms with editable shapes and connectors. See [the preview index](drawio/README.md).

The recommendation is **DisruptionOps for incident coordination, five field coordination cells for a 100-aircraft fleet, and onboard autonomy for each aircraft**. A cell is a group of aircraft and its ground edge coordinator, not a formation that must fly together. Five groups of twenty is a sizing example, not an established operating standard.

## Diagrams

The complete Graphviz overview covering all five mechanisms is [05-complete-swarm-system.svg](05-complete-swarm-system.svg), with [editable DOT](05-complete-swarm-system.dot) and [PNG](05-complete-swarm-system.png).

| View | Editable Graphviz | Vector render | Image render |
|---|---|---|---|
| Whole architecture and authority boundaries | [01-architecture.dot](01-architecture.dot) | [SVG](01-architecture.svg) | [PNG](01-architecture.png) |
| Assam field deployment and fleet rotation | [02-assam-deployment.dot](02-assam-deployment.dot) | [SVG](02-assam-deployment.svg) | [PNG](02-assam-deployment.png) |
| Observation → incident → mission → outcome | [03-mission-lifecycle.dot](03-mission-lifecycle.dot) | [SVG](03-mission-lifecycle.svg) | [PNG](03-mission-lifecycle.png) |

The arrows identify logical information flows, not guarantees that a radio or cloud link is available. The aircraft stack is expanded once in the first diagram; it exists on every aircraft.

## Supporting material

- [Repository integration notes](repository-integration.md): existing behavior, specific files, and proposed contracts.
- [Research sources](sources.md): primary sources, what each supports, and limits of the evidence.
- [Review record](review-record.json): repository commits and inspected files.

To regenerate using Graphviz on PATH:

```powershell
Set-Location D:\drone_swarm\docs\research\100-drone-disaster-response
Get-ChildItem -Filter *.dot | ForEach-Object {
    if ($_.BaseName -eq '05-complete-swarm-system') {
        neato -n2 -Tsvg $_.FullName -o ($_.BaseName + '.svg')
        neato -n2 -Tpng -Gdpi=120 $_.FullName -o ($_.BaseName + '.png')
    } else {
        dot -Tsvg $_.FullName -o ($_.BaseName + '.svg')
        dot -Tpng -Gdpi=120 $_.FullName -o ($_.BaseName + '.png')
    }
}
```

PNG files are ignored by the repository's existing `*.png` rule. DOT and SVG are the portable version-controlled artifacts. A checksum-verified portable Graphviz 12.2.1 was used from the ignored `.tools/graphviz` directory; no system installation was required.
