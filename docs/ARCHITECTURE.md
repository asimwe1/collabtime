# Warehouse Simulation Architecture

## Current System Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          ORCHESTRATION LAYER                                 │
│                                                                              │
│  ┌──────────────────────┐              ┌──────────────────────────────┐    │
│  │   Dashboard (Dash)   │              │   LF Coordinator (optional)  │    │
│  │  - Web UI controls   │◄────────────►│   - Deterministic ticks      │    │
│  │  - Visualization     │   Optional   │   - Logical time             │    │
│  │  - Metrics display   │   LF mode    │   - TCP bridge (port 9001)   │    │
│  └──────────┬───────────┘              └────────────┬─────────────────┘    │
│             │                                        │                       │
│             │ start/stop/step                        │ tick events           │
│             └────────────────┬───────────────────────┘                       │
│                              │                                               │
└──────────────────────────────┼───────────────────────────────────────────────┘
                               │
                               ▼
    ┌─────────────────────────────────────────────────────────────────────────────┐
    │                            SIMULATION CORE                                   │
    │                                                                              │
    │  ┌─────────────────────────────────────────────────────────────────────┐   │
    │  │                      WarehouseDSMModel (Mesa)                        │   │
    │  │  - Orchestrates agents, tasks, warehouse graph                       │   │
    │  │  - Poisson task generation (event-driven)                            │   │
    │  │  - Edge reservation for collision avoidance                          │   │
    │  │  - Data collection (Mesa DataCollector)                              │   │
    │  │  - Step loop: tick coordinator → generate → step → cleanup → gossip │   │
    │  └────┬───────────────┬──────────────────┬────────────────┬─────────────┘   │
    │       │               │                  │                │                 │
    │       │               │                  │                │                 │
    │    agents       coordinator          warehouse         pathfinder          │
    │       │               │                  │                │                 │
    │       ▼               ▼                  ▼                ▼                 │
    │  ┌─────────┐   ┌─────────────┐   ┌──────────┐    ┌──────────┐            │
    │  │ Robot   │   │ Coordinator │   │ Warehouse│    │   A*     │            │
    │  │ Agent   │   │ (Control    │   │  Graph   │    │ Routing  │            │
    │  │ (Mesa)  │   │  Plane)     │   │(NetworkX)│    │   w/     │            │
    │  └─────────┘   └─────────────┘   └──────────┘    │ Costs    │            │
    │                                                    └──────────┘            │
    └─────────────────────────────────────────────────────────────────────────────┘
                                │
                                │
                               ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           CONTROL PLANE                              │
│                                                                              │
│  ┌────────────────────────────────────────────────────────────────────┐    │
│  │                    Coordinator (Strong Consistency)                 │    │
│  │                                                                     │    │
│  │  Components:                                                        │    │
│  │  ┌──────────────────────────────────────────────────────────────┐  │    │
│  │  │  TaskRegistry (Authoritative)                                │  │    │
│  │  │  - Task lifecycle: AVAILABLE → CLAIMED → COMPLETED          │  │    │
│  │  │  - create_task(), claim_task(), complete_task()             │  │    │
│  │  │  - Strong consistency, no stale reads                       │  │    │
│  │  └──────────────────────────────────────────────────────────────┘  │    │
│  │                                                                     │    │
│  │  ┌──────────────────────────────────────────────────────────────┐  │    │
│  │  │  LeaseManager (Exclusive Locks)                              │  │    │
│  │  │  - Task locks (long-term, minutes)                           │  │    │
│  │  │  - Resource locks (JIT, seconds)                             │  │    │
│  │  │  - TTL-based expiration, automatic recovery                  │  │    │
│  │  └──────────────────────────────────────────────────────────────┘  │    │
│  │                                                                     │    │
│  │  ┌──────────────────────────────────────────────────────────────┐  │    │
│  │  │  WatchManager (Event Notifications)                          │  │    │
│  │  │  - task_created, task_claimed, task_released, completed      │  │    │
│  │  │  - ZooKeeper-style watches                                   │  │    │
│  │  └──────────────────────────────────────────────────────────────┘  │    │
│  │                                                                     │    │
│  │  ┌──────────────────────────────────────────────────────────────┐  │    │
│  │  │  MembershipTracker (Failure Detection)                       │  │    │
│  │  │  - Heartbeat monitoring, auto-release on failure             │  │    │
│  │  └──────────────────────────────────────────────────────────────┘  │    │
│  └────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  API: try_claim(task_id, agent_id, ttl) → bool                             │
│       acquire_lock(resource_id, agent_id, ttl) → bool                       │
│       complete_task(task_id, agent_id) → bool                               │
│       tick(current_time_ms) - expire leases, detect failures                │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
                               │
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           DATA PLANE                                         │
│                                                                              │
│  ┌────────────────────────────────────────────────────────────────────┐    │
│  │                     DSM (Eventual Consistency)                      │    │
│  │                                                                     │    │
│  │  MODE: CENTRALIZED                    MODE: DISTRIBUTED            │    │
│  │  ┌────────────────────────┐           ┌──────────────────────┐    │    │
│  │  │   Single DSM Instance  │           │    DSMRouter         │    │    │
│  │  │   - 2 Layers           │           │   ┌──────┬──────┐    │    │    │
│  │  │     • flow_trace       │           │   │Shard0│Shard1│... │    │    │
│  │  │     • jam_signal       │           │   │      │      │    │    │    │
│  │  │   - Perfect visibility │           │   │ DSM  │ DSM  │    │    │    │
│  │  │   - Zero coordination  │           │   └──────┴──────┘    │    │    │
│  │  └────────────────────────┘           │   - Halo gossip      │    │    │
│  │                                        │   - AoI filtering    │    │    │
│  │  (task_signal REMOVED)                │   - ID encoding      │    │    │
│  │  (tasks now in Coordinator)           └──────────────────────┘    │    │
│  └────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  DSMLayers: read_window(layer, node, radius, max_aoi_ms) → window          │
│             write_delta(layer, {node: value}, timestamp_ms)                 │
│  (Only spatial data: flow_trace, jam_signal)                                │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
                               │
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           AGENT BEHAVIOR                                     │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                        RobotAgent State Machine                      │   │
│  │                                                                      │   │
│  │   ┌──────┐   claim task   ┌────────────┐  arrive  ┌─────────┐      │   │
│  │   │ IDLE ├───────────────►│ NAVIGATING ├─────────►│ WORKING │      │   │
│  │   └───┬──┘                └──────┬─────┘          └────┬────┘      │   │
│  │       │                          │                     │            │   │
│  │       │ scan tasks               │ move step-by-step   │ work timer │   │
│  │       │ choose nearest           │ A* pathfinding      │ complete   │   │
│  │       │ try claim (CAS)          │ edge reservations   │            │   │
│  │       │                          │ lateral escape      │            │   │
│  │       │                          │ jam detection       │            │   │
│  │       │                          │                     │            │   │
│  │       └──────────────────────────┴─────────────────────┘            │   │
│  │                         (back to IDLE)                              │   │
│  │                                                                      │   │
│  │  Decision loop (IDLE):                                              │   │
│  │   1. Read task_registry.tasks (all tasks across shards)             │   │
│  │   2. Filter: available, not in cooldown, location free              │   │
│  │   3. Prefer nearby tasks (locality bonus if same shard)             │   │
│  │   4. Try claim (up to 3 attempts with fallback)                     │   │
│  │   5. On success → plan path → NAVIGATING                            │   │
│  │   6. On failure → wander to staging area                            │   │
│  │                                                                      │   │
│  │  Movement (NAVIGATING):                                             │   │
│  │   1. Use A* with DSM congestion costs (flow_trace, jam_signal)      │   │
│  │   2. Reserve edge before move (collision avoidance)                 │   │
│  │   3. Write flow_trace on each step                                  │   │
│  │   4. If stuck > threshold → write jam_signal, fail task             │   │
│  │   5. Lateral escape if blocked (one-step detour)                    │   │
│  │                                                                      │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
                               │
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         CONSISTENCY & TIMING                                 │
│                                                                              │
│  Current Consistency Model:                                                 │
│   - Task claims: Sequential in-process CAS (per-shard)                      │
│   - Task visibility: Global merged view (RouterTaskRegistry cache)          │
│   - Layer reads: Eventual consistency + AoI filtering                       │
│   - Gossip: Periodic boundary exchange (150ms default)                      │
│                                                                              │
│  Timing:                                                                    │
│   - Step duration: 0.05s (LF mode) or 0.5s (internal)                      │
│   - Movement: 5 steps/cell = ~5s/cell (careful navigation)                 │
│   - Work: 30-60s per task                                                   │
│   - Gossip period: 150ms                                                    │
│   - AoI threshold: 1000ms (default)                                         │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Data Flow

### Task Lifecycle (NEW - with Coordinator)
```
1. Model.step() → coordinator.tick(current_time_ms) [expire leases]
   ↓
2. Model._generate_tasks() → coordinator.create_task(location)
   ↓ (WatchManager fires 'task_created' event)
3. Agent (IDLE) → coordinator.get_available_tasks()
   ↓
4. Agent chooses task → coordinator.try_claim(task_id, agent_id, ttl=300s)
   ↓ (LeaseManager: atomic CAS, TaskRegistry: status = 'claimed')
5. Agent → plan path → NAVIGATING
   ↓
6. Agent moves step-by-step, writes flow_trace to DSM
   ↓
7. Agent arrives at location
   ↓
8. coordinator.acquire_lock(node_resource_id, agent_id, ttl=30s) [JIT lock]
   ↓ (If locked → wait or fail task)
9. If lock acquired → WORKING (work_timer countdown)
   ↓
10. Work complete → coordinator.release_lock(node_resource_id)
    ↓
11. coordinator.complete_task(task_id, agent_id)
    ↓ (Release lease, update status = 'completed')
12. Model._cleanup_tasks() → moves to completed_tasks[]
```

### Gossip Flow (Distributed Mode Only)
```
Every gossip_period_ms (150ms):
  1. DSMRouter.periodic_gossip(current_time_ms)
  2. For each boundary_node with data:
     - Read from source shard layer
     - For each neighbor shard:
       - If timestamp newer → copy data
       - messages_sent++
  3. Update stats: gossip_messages, coordination_time
```

### Pathfinding with DSM
```
Agent needs path from A to B:
  1. astar_with_congestion(warehouse, dsm_api, start, goal, cost_params)
  2. For each candidate edge:
     - compute_edge_cost():
       • read_window('jam_signal', node, radius=2, max_aoi_ms)
       • read_window('flow_trace', node, radius=2, max_aoi_ms)
       • cost = base_cost + alpha*jam + beta*flow
  3. Return path (list of nodes)
  4. Agent: reserve edges → move → write flow_trace
```

## Key Invariants

1. **Determinism**: seed + config → reproducible runs
2. **Task atomicity**: Only one agent can claim a given task
3. **Safety**: Node capacity enforced; head-on collisions prevented via edge reservations
4. **AoI enforcement**: Reads filter stale data beyond aoi_threshold
5. **Task lifecycle**: created → available → claimed → completed|failed|expired

## Performance Improvements (With Coordinator)

1. **Eliminated claim conflicts**:
   - ✓ Coordinator provides atomic CAS-style claims via LeaseManager
   - ✓ Strong consistency for task state (zero stale reads)
   - ✓ Measured: claim_conflicts = 0 in tests

2. **JIT resource locking**:
   - ✓ Agents acquire node locks only when arriving (30s TTL)
   - ✓ Prevents simultaneous work at same location
   - ✓ Automatic release on completion or lease expiration

3. **Automatic lease recovery**:
   - ✓ If agent fails/gets stuck, lease expires automatically
   - ✓ Task reverts to AVAILABLE, other agents can claim
   - ✓ No manual cleanup required

4. **Event notification infrastructure**:
   - ✓ WatchManager ready for event-driven agents
   - ✓ Agents can subscribe to task_created events (no polling)
   - ✓ Future: Replace polling with reactive watches

5. **Separation of concerns**:
   - ✓ Control plane: Low-volume, strong consistency (tasks, leases)
   - ✓ Data plane: High-volume, eventual consistency (flow, congestion)
   - ✓ Each optimized for its purpose

## Module Summary

### Core Modules

**`world/`** - Simulation core, agents, warehouse graph
- `model.py` - Mesa model, task generation, orchestration
- `agent.py` - Robot agent state machine and behavior
- `graph.py` - Warehouse topology (NetworkX)

**`coord/`** - Coordination layer (control plane)
- `coordinator.py` - ZooKeeper-style coordinator facade
- `lease_manager.py` - Exclusive locks with TTL expiration
- `task_registry.py` - Authoritative task state (strong consistency)
- `watch_manager.py` - Event notification system
- `membership.py` - Heartbeat-based failure detection

**`dsm/`** - Distributed shared memory system (data plane)
- `api.py` - Single DSM instance with layers (flow_trace, jam_signal)
- `router.py` - Multi-shard router with gossip protocol
- `partition.py` - Spatial/spectral partitioning strategies

**`pathfinder/`** - Congestion-aware routing
- `routing.py` - A* pathfinding with DSM costs
- `costs.py` - Edge cost computation (jam, flow signals)

### Interface Modules

**`dashboard/`** - Web UI and visualization
- `app.py` - Dash application entry point
- `state.py` - Global state, simulation loop
- `callbacks.py` - UI event handlers
- `visualization.py` - Rendering utilities

**`lf/`** - Lingua Franca coordination (optional)
- `coordinator.lf` - LF reactor definitions
- `bridge.py` - TCP client for tick synchronization

### Experiment Infrastructure

**`experiments/`** - Batch runner and configuration
- `run.py` - Experiment orchestrator
- `configs.yaml` - Experiment parameter sweeps

**`config.py`** - Global simulation parameters
- Timing constants (step duration, movement, work)
- Capacity calculations
- Search radii, timeouts, retries

## Configuration Modes

| Mode | DSM Type | Task View | Gossip | Use Case |
|------|----------|-----------|--------|----------|
| `centralized` | Single DSM instance | Perfect, instant | None | Baseline, theoretical max |
| `distributed` | DSMRouter (4 shards) | Global cached | 150ms halo | Realistic, scalability study |

## Metrics Collected

- **Tasks**: created, completed, failed, active, completion_time
- **Agents**: utilization, distance_traveled, state distribution
- **DSM**: reads, writes, gossip_messages, aoi_violations, coordination_time
- **System**: throughput (tasks/sec), T50/T90 latency, stability region

