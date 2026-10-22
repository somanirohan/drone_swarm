# Real-time drone swarm — editable draw.io diagram

[Open the complete six-page diagram](50-drone-realtime-system.drawio). Its first page is the overview; the other five explain each mechanism in detail. All boxes, labels, groups and connectors are editable.

Created using the official [jgraph/drawio-mcp](https://github.com/jgraph/drawio-mcp) project's native XML workflow and `@drawio/mcp` server. Its `open_drawio_xml` tool was called through an MCP client to open the full diagram in the browser editor. Its `list_pages` tool checks the local six-page document. PNG/SVG previews were exported using checksum-verified draw.io Desktop 32.4.1. No permanent Codex MCP configuration or global installation was changed.

| Page | Preview | Editable single-page file |
|---|---|---|
| Overview: all five mechanisms | [SVG](01-overview.drawio.svg) · [PNG](01-overview.drawio.png) | [draw.io](01-overview.drawio) |
| Onboard sensing, estimation, planning and feedback control | [SVG](02-onboard-control.drawio.svg) · [PNG](02-onboard-control.drawio.png) | [draw.io](02-onboard-control.drawio) |
| State/trajectory sharing and field communication | [SVG](03-communication.drawio.svg) · [PNG](03-communication.drawio.png) | [draw.io](03-communication.drawio) |
| Failure detection, uncertain state and work recovery | [SVG](04-failure-recovery.drawio.svg) · [PNG](04-failure-recovery.drawio.png) | [draw.io](04-failure-recovery.drawio) |
| Energy measurement, prediction and sortie rotation | [SVG](05-energy-budget.drawio.svg) · [PNG](05-energy-budget.drawio.png) | [draw.io](05-energy-budget.drawio) |
| Persistent surveillance and task allocation | [SVG](06-surveillance-allocation.drawio.svg) · [PNG](06-surveillance-allocation.drawio.png) | [draw.io](06-surveillance-allocation.drawio) |

The diagram describes a proposed humanitarian system, not an implemented or certified fleet. It distinguishes local flight control from event-driven mission allocation, unreachable aircraft from confirmed crashes, and observations from confirmed aid/extraction outcomes. Timing and energy examples are labeled accordingly. The cited research and its limits are in [the source register](../sources.md).

PNG/SVG exports embed native diagram XML. The `.drawio` originals are retained for direct editing and the multi-page document. The existing repository ignores PNG files; SVG and `.drawio` files remain version-controllable.
