> **Recommended path:** use the integration kit, `docs/INTEGRATION-KIT.md` and the drop-in client `swarm_ops/integrations/client/swarm_client.py`. The patch below is the older bridge-based approach.

# DisruptionOps patch proposal (for upstream f7ca6d8, migrations 001-038)

Nothing here is applied to DisruptionOps. Copy the files into a DisruptionOps checkout when you want them.

| File | What it is |
|---|---|
| `backend/migrations/040_drone_swarm_agency.sql` | Data only. Registers agency `drone-swarm` and capabilities `aerial_assessment`, `supply_drop`, `relay`. No `drone` resource kind: `agents/replan.py:_air_matrix` skips kind `drone`, so the CP-SAT planner would price a drone on roads. |
| `backend/app/drone/swarm_agency.py` | Sends `agency_request` to the swarm over its WebSocket and ingests its reports through existing doors (`world.events.append`, `reports.request_agency` / `respond_to_agency_request`, `incidents.intake.receive`). Honours `autonomy.paused()` and `operator_overrides`. |
| `backend/migrations/039_swarm_operations_demo.sql` and `backend/app/drone/operations.py` | The older, optional demo-database adapter (renumbered from 030, which upstream now uses). Dev database only. |

## Wire it in (3 edits in DisruptionOps)

1. Apply `040_drone_swarm_agency.sql` (after 038) and `backend/patches/agency_replies_external_agencies.diff` (`git apply`, 16 lines: makes `drone-swarm` an external agency so `ops/agency_replies` neither answers it at random nor demotes its acceptance to a decline).
2. `backend/app/main.py`, in `lifespan` next to `drone_swarm.start()` / `.stop()`:
   ```python
   from app.drone import swarm_agency
   await swarm_agency.start()      # ... and `await swarm_agency.stop()` on shutdown
   ```
3. Optional status route in `backend/app/api/v1/drone.py`:
   ```python
   @router.get("/drone/swarm-agency")
   async def swarm_agency_status(_: StaffPrincipal) -> dict:
       from app.drone import swarm_agency
       return swarm_agency.status()
   ```

## Environment on the DisruptionOps side

```
SWARM_AGENCY=1
SWARM_WS_URL=wss://<swarm-host>/ops/ws
SWARM_INTEGRATION_TOKEN=<same value as the swarm's OPS_INTEGRATION_TOKEN>
SWARM_ANCHOR=18.6526632,73.7624378     # optional
```

The old `DRONE_SWARM` listener (`app/drone/swarm.py`, PyBullet feed) is a different feed. Leave it off (`DRONE_SWARM=0`) unless you also run that feed.

The module's tests (`tests/test_swarm_agency_bridge.py`) run it against the real swarm server with fake DisruptionOps doors.
