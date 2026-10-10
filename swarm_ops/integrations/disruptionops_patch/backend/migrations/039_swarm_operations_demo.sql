-- Optional adapter: apply ONLY to a separate development/demo database. Renumbered for upstream f7ca6d8 (migrations 001-038).
begin;
create table if not exists swarm_operations_events (
  event_id text primary key,
  run_id text not null,
  city_id text not null references cities(id),
  body_hash text not null,
  local_incident_id text,
  incident_id uuid references incidents(id),
  response jsonb,
  payload jsonb not null,
  created_at timestamptz not null default now()
);
create index if not exists swarm_operations_run on swarm_operations_events(run_id, city_id);
create table if not exists swarm_operations_handoffs (
  run_id text not null,
  local_incident_id text not null,
  resource_id text not null references resources(id),
  briefing_revision integer not null check (briefing_revision > 0),
  accepted_by text not null,
  accepted_at timestamptz not null default now(),
  primary key (run_id, local_incident_id, resource_id, briefing_revision)
);
alter table swarm_operations_events enable row level security;
alter table swarm_operations_handoffs enable row level security;
revoke all on swarm_operations_events, swarm_operations_handoffs from anon, authenticated;
commit;
