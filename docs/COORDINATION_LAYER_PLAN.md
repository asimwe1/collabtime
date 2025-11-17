# Coordination Layer Design Plan

## Motivation

The current warehouse simulation conflates data-plane (tile signals, flows) with control-plane (task claims, notifications). This causes:

1. **Stale decisions**: Agents choose tasks based on old layer data (AoI violations)
2. **Claim conflicts**: Multiple agents claim same task due to eventual consistency
3. **No event notifications**: Agents poll task list; no "watch" for new tasks in region
4. **Mixed semantics**: DSM uses gossip (eventual) for both high-volume data and exclusive claims

### Solution: Separation of Concerns

- **Data Plane (DSM)**: High-volume spatial data with eventual consistency
  - `flow_trace`, `jam_signal`, `path_intent`
  - Gossip + CRDT merge (peer-to-peer)
  - NO `task_signal` (Watch Manager handles this)

- **Control Plane (Coordinator)**: Low-volume metadata with strong semantics
  - Task leases (exclusive claims)
  - Watches (event notifications)
  - Acquire/Release barriers (LRC-style freshness)
  - Membership/heartbeats

This mirrors ZooKeeper's design: small, strongly-consistent coordination service separate from large data stores.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        WarehouseDSMModel (Mesa)                              │
│  - Orchestration, task generation, edge reservations                         │
│  - Step loop: tick coordinator → step agents → gossip DSM                   │
└────────┬──────────────────────────────────────────────────┬──────────────────┘
         │                                                  │
         │ coordinator.tick(current_time_ms)                │ dsm.periodic_gossip()
         │                                                  │
         ▼                                                  ▼
┌─────────────────────────────────────────┐   ┌───────────────────────────────┐
│      Coordinator (Control Plane)        │   │   DSM/Router (Data Plane)     │
│                                         │   │                                │
│  ┌───────────────────────────────────┐  │   │  ┌──────────────────────────┐ │
│  │  Task Registry (Strong)           │  │   │  │  DSMLayers (CRDT)        │ │
│  │  - tasks dict (authoritative)     │  │   │  │  - flow_trace            │ │
│  │  - create_task()                  │  │   │  │  - jam_signal            │ │
│  │  - get_available_tasks()          │  │   │  │  - path_intent           │ │
│  │  - update_status()                │  │   │  │  - agent_location        │ │
│  └───────────────────────────────────┘  │   │  │  - path_intent (NEW)     │ │
│                                         │   │  │  - read_window()         │ │
│  ┌───────────────────────────────────┐  │   │  │  - write_delta()         │ │
│  │  Lease Manager                    │  │   │  │  - Halo gossip           │ │
│  │  - Task locks (long-term)         │  │   │  └──────────────────────────┘ │
│  │  - Resource locks (JIT, short)    │  │   │                                │
│  │  - try_claim(task, agent, ttl)    │  │   │                                │
│  │  - acquire_lock(resource, ttl)    │  │   │                                │
│  │  - renew, release, auto-expire    │  │   │                                │
│  └───────────────────────────────────┘  │   │                                │
│                                         │   │                                │
│  ┌───────────────────────────────────┐  │   │                                │
│  │  Watch Manager                    │  │   │                                │
│  │  - watch(region, event, callback) │  │   │                                │
│  │  - unwatch(handle)                │  │   │                                │
│  │  - fire_event(region, event, ...)│  │   │                                │
│  └───────────────────────────────────┘  │   │                                │
│                                         │   │                                │
│  ┌───────────────────────────────────┐  │   │                                │
│  │  Membership Tracker (optional)    │  │   │                                │
│  │  - register_agent(), heartbeat()  │  │   │                                │
│  │  (Forces immediate gossip round)  │  │   │                                │
│  └───────────────────────────────────┘  │   │                                │
│                                         │
│  ┌───────────────────────────────────┐  │
│  │  Membership (optional)            │  │
│  │  - register_agent(agent_id)       │  │
│  │  - heartbeat(agent_id)            │  │
│  │  - detect_failures()              │  │
│  └───────────────────────────────────┘  │
│                                         │
│  Metrics:                               │
│  - claim_conflicts                      │
│  - lease_expirations                    │
│  - acquire_latency_ms                   │
│  - watch_events_fired                   │
└─────────────────────────────────────────┘
         ▲
         │ coordinator calls
         │
┌────────┴──────────────────────────────────────────────────────────────────┐
│                          RobotAgent (IDLE handler)                         │
│                                                                            │
│  OLD (current):                                                            │
│   1. scan dsm.task_registry.tasks                                          │
│   2. choose task                                                           │
│   3. dsm.claim(task_id, agent_id) → bool                                   │
│   4. if success → NAVIGATING                                               │
│                                                                            │
│  NEW (event-driven with coordinator):                                      │
│   1. On IDLE: watch_handle = coordinator.watch(region, 'task_created',    │
│                                                 self.on_new_task)          │
│   2. Also do initial scan: self.on_new_task() (in case tasks exist)       │
│                                                                            │
│   3. on_new_task() callback (triggered by WatchManager):                  │
│      - tasks = coordinator.get_available_tasks(self.region)               │
│      - if not tasks: return                                                │
│      - chosen = self.choose_best(tasks)                                    │
│      - if coordinator.try_claim(chosen.id, self.unique_id, ttl):          │
│          * coordinator.unwatch(self.watch_handle)  # Stop watching        │
│          * self.state = NAVIGATING                                         │
│                                                                            │
│  Key: Watch Manager IS the task_signal. Remove task_signal from DSM.      │
│       No polling. Agents react to events only.                            │
└────────────────────────────────────────────────────────────────────────────┘
```

---

## Three-Tier Locking Strategy

### Critical Design Question: What Do We Lock?

The system uses **three different locking mechanisms** for three different purposes. This separation is key to scalability.

### 1. Task Locks (Control Plane - Long-Term)

**Purpose**: Claim logical responsibility for a high-level job

**Managed by**: `Coordinator.LeaseManager`

**Granularity**: Coarse-grained (entire task)

**Duration**: Long-term (minutes) - from claim until completion

**Example**:
```python
# Robot A claims task_123 (pick item from storage node 42)
coordinator.try_claim(task_id=123, agent_id=A, ttl_ms=300_000)  # 5 min lease

# Robot A now "owns" this task for up to 5 minutes
# But this does NOT lock node 42 yet!
```

**Why separate from physical resources?**
- Robot might take 2-3 minutes to navigate to storage node 42
- If we locked node 42 now, it would be unavailable for 3+ minutes
- Robot B might have a different task also requiring node 42
- Robot B would be blocked unnecessarily

### 2. Physical Resource Locks (Control Plane - Just-In-Time)

**Purpose**: Claim exclusive physical access to stationary shared resources

**Managed by**: `Coordinator.LeaseManager` (same component, different namespace)

**Granularity**: Fine-grained (individual bins, charging stations, elevators)

**Duration**: Very short-term (seconds) - only while actively using the resource

**Resources that need JIT locking**:
- Storage nodes (while picking items from storage region)
- Sortation nodes (while placing items in sortation region)
- Charging stations (while charging in staging area)

Note: In our warehouse topology, bins are just graph nodes in storage/sortation regions. We don't model elevators or narrow doorways - the warehouse is a flat 2D grid with storage, sortation, and staging (charging) areas.

**Example workflow**:
```python
# Robot A navigation phase (storage node 42 is NOT locked)
def _handle_navigating(self):
    # ... moving towards storage node 42 ...
    
    if self.node == self.task_location:  # Arrived at storage node
        # NOW we try to lock the physical node
        coordinator = self.model.coordinator
        node_resource_id = f"node_{self.task_location}"
        
        # Try to acquire short-term physical lock
        lock_success = coordinator.acquire_lock(
            resource_id=node_resource_id,
            agent_id=self.unique_id,
            ttl_ms=30_000  # 30 seconds - just enough for pick operation
        )
        
        if lock_success:
            self.state = AgentState.WORKING
            self.work_timer = self.work_duration
        else:
            # Another robot is using this node right now
            # Wait or try lateral escape
            self.wait_for_resource_timer = 50  # ~2.5 seconds
            # After timeout, retry lock acquisition
```

**Why JIT (Just-In-Time)?**
- Storage/sortation node is only locked for ~30 seconds during actual pick/place operation
- Other robots can navigate towards the same node while Robot A is working
- Maximizes resource utilization
- Minimizes contention

**Lock release**:
```python
def _handle_working(self):
    if self.work_timer <= 0:
        # Work complete - release physical lock immediately
        coordinator = self.model.coordinator
        node_resource_id = f"node_{self.task_location}"
        coordinator.release_lock(node_resource_id, self.unique_id)
        
        # Also release task lease
        coordinator.release_claim(self.current_task_id, self.unique_id)
        
        self.state = AgentState.IDLE
```

### 3. Path & Space Deconfliction (Data Plane - Decentralized)

**Purpose**: Avoid collisions and congestion in aisles and intersections

**Managed by**: DSM layers + agent local planning (NO central lock server)

**Granularity**: Dynamic (per-edge, per-cell)

**Duration**: Ephemeral (seconds) - only while robot is traversing

**Why NOT use control-plane locks for paths?**
- Warehouse has 300-500 nodes × 1000 edges
- If every edge required a lock request to coordinator → central bottleneck
- This is exactly what OpenRMF does, and it doesn't scale beyond ~50 robots
- Your key innovation: **decentralized path deconfliction via gossip**

**How it works (Data Plane)**:

**Step 1: Robot publishes intent**
```python
# Robot A plans a path
def _plan_path(self, from_node, to_node):
    path = astar_with_congestion(...)
    
    # Publish path intent to DSM (gossip to neighbors)
    current_time_ms = int(time.time() * 1000)
    for i, node in enumerate(path):
        arrival_time_ms = current_time_ms + (i * movement_duration_ms)
        
        # Write "I will be at node X at time T"
        dsm_api.write_delta('path_intent', {
            node: {
                'agent_id': self.unique_id,
                'arrival_time': arrival_time_ms,
                'duration': movement_duration_ms
            }
        }, current_time_ms)
    
    return path
```

**Step 2: Other robots see intent via gossip**
```python
# Robot B planning a path (happens ~100ms later after gossip)
def compute_edge_cost(edge, dsm_api, current_time_ms):
    base_cost = edge_length
    
    # Read path intents from other agents
    window = dsm_api.read_window('path_intent', edge.to_node, radius=2, max_aoi_ms=500)
    
    for node_id, data in window['window_data'].items():
        intent = data['value']
        other_agent = intent['agent_id']
        
        # Will this other robot be here when I arrive?
        if abs(intent['arrival_time'] - my_arrival_time) < collision_threshold:
            # Yes - add penalty to avoid this edge
            base_cost += congestion_penalty
    
    return base_cost
```

**Step 3: No central coordination needed**
- Robots independently plan around each other's published intents
- Gossip ensures intents propagate within 150-300ms
- Old intents expire (AoI filtering)
- No single point of failure

**Fallback: Local edge reservations**
- For head-on collision prevention, model maintains short-term edge reservations
- These are in-memory, not via coordinator (too fast/high-frequency)
- See `model.try_reserve_edge()` in current code

### Comparison Table

| Lock Type | Purpose | Managed By | Duration | Frequency | Scalability Impact |
|-----------|---------|------------|----------|-----------|-------------------|
| **Task Lock** | Claim job responsibility | Control Plane | Long (minutes) | Low (~0.3/sec) | Minimal |
| **Resource Lock** | Exclusive physical access | Control Plane | Short (seconds) | Low (~0.3/sec) | Minimal |
| **Path Intent** | Collision avoidance | Data Plane (Gossip) | Ephemeral (seconds) | High (~20/sec/agent) | **Scales!** |

### Why This Design Scales

**Centralized (OpenRMF-style)**:
- All three lock types go through central scheduler
- Path locks alone: 8 agents × 20 steps/sec × 5 nodes/path = **800 lock requests/sec**
- Central bottleneck

**Your Distributed Model**:
- Only tasks + resources go through coordinator
- 8 agents × 0.3 tasks/sec × 2 locks/task = **~5 lock requests/sec**
- Path intents use gossip (decentralized)
- No central bottleneck

### Implementation Notes

1. **Namespace separation** in LeaseManager:
   - Task locks: `resource_id = task_id`
   - Resource locks: `resource_id = f"node_{location}"` or `f"charger_{id}"`

2. **TTL guidelines**:
   - Task locks: 5-10 minutes (entire task duration)
   - Resource locks: 30-60 seconds (just the work operation)
   - Path intents: No TTL needed (AoI filter removes stale)

3. **Metrics to collect**:
   - `task_lock_conflicts` (should be low)
   - `resource_lock_conflicts` (may be higher, indicates node contention)
   - `resource_wait_time` (time spent waiting for node lock)
   - `path_replans_due_to_congestion` (from gossip-based avoidance)

### The Critical Innovation: Distributed Pathfinding

**This is your key research contribution over centralized systems like OpenRMF/MindAgent.**

**Centralized Architecture (OpenRMF)**:
```
┌─────────────────────────────────────┐
│   Central Traffic Coordinator      │
│   - Runs ONE A* pathfinder          │
│   - Computes paths for ALL robots   │
│   - Locks ALL edges centrally       │
│   - Bottleneck: O(N robots)         │
└─────────────────────────────────────┘
         ↑ ↑ ↑ ↑ ↑ ↑ ↑ ↑
         │ │ │ │ │ │ │ │
      8 robots all waiting for paths
```

**Your Peer-to-Peer Architecture (CRDTs + Gossip)**:
```
┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│  Robot A     │  │  Robot B     │  │  Robot C     │  │  Robot D     │
│              │  │              │  │              │  │              │
│ local_cache: │  │ local_cache: │  │ local_cache: │  │ local_cache: │
│  {           │  │  {           │  │  {           │  │  {           │
│   "aisle_1": │  │   "aisle_1": │  │   "aisle_2": │  │   "aisle_2": │
│     GCounter │  │     GCounter │  │     GCounter │  │     GCounter │
│     LWWMap   │  │     LWWMap   │  │     LWWMap   │  │     LWWMap   │
│  }           │  │  }           │  │  }           │  │  }           │
│              │  │              │  │              │  │              │
│ Local A*     │  │ Local A*     │  │ Local A*     │  │ Local A*     │
└──────────────┘  └──────────────┘  └──────────────┘  └──────────────┘
       ↕ gossip ↔        ↕ gossip ↔        ↕ gossip ↔        ↕
    (peer-to-peer, epidemic protocol, ~150ms rounds)
    (CRDT merge: no conflicts, eventual consistency)

NO central shards. NO ground truth.
Each agent is a peer with its own cache.
Gossip protocol picks random pairs to merge.
CRDTs ensure conflict-free convergence.
```

**How Distributed Pathfinding Works**:

1. **Each robot has its own A* pathfinder** (method in `RobotAgent`)
   ```python
   def _plan_path(self, from_node, to_node):
       # This runs LOCALLY on the robot
       path = astar_with_congestion(
           warehouse=self.model.warehouse,  # Static map (shared)
           dsm_api=dsm_api,                 # Local DSM cache (gossiped)
           start=from_node,
           goal=to_node,
           cost_params={'alpha': 2.0, 'beta': 0.5}
       )
       return path
   ```

2. **Robot reads static map** (shared, unchanging)
   - Graph topology from `self.model.warehouse`
   - Node positions, edge connectivity
   - Storage/sortation/staging regions

3. **Robot reads dynamic state from its own local_cache** (gossiped from peers, possibly stale)
   - `jam_signal`: Where are other robots stuck?
   - `flow_trace`: What edges are heavily used?
   - `path_intent`: Where will other robots be?
   - **Key**: This data may be ~100-300ms stale (from peer gossip), but that's acceptable!
   - Robot reads from its own CRDT cache (instant, no network call)

4. **Robot computes path locally**
   ```python
   def compute_edge_cost(self, edge):
       base_cost = edge_length
       
       # Read congestion from OWN local cache (CRDT, stale is OK)
       shard_key = self.get_shard_for_node(edge.to_node)
       jam = self.local_cache[shard_key]["jam_levels"].get(edge.to_node, 0.0)
       flow = self.local_cache[shard_key]["flow_counts"].value(edge.id)
       
       # Add penalties based on local view
       jam_cost = alpha * jam
       flow_cost = beta * flow
       
       return base_cost + jam_cost + flow_cost
   ```

5. **Robot publishes its chosen path** (for other robots to avoid)
   ```python
   # After planning, write path intent to OWN local cache
   for node in path:
       shard_key = self.get_shard_for_node(node)
       self.local_cache[shard_key]["path_intents"].set(
           (self.unique_id, node),
           {'agent_id': self.unique_id, 'arrival_time': ...},
           timestamp=self.current_time_ms
       )
   
   # This will gossip to peers within 150ms
   ```

**Why This Scales**:
- **Computation distributed**: Each robot runs its own A*, no central bottleneck
- **Data distributed**: Peer-to-peer CRDTs, no central shard servers
- **Stale data acceptable**: Paths don't need perfect info; 300ms staleness is fine
- **No coordinator locks for paths**: Only tasks/resources use coordinator

**Comparison**:
| System | Pathfinder Location | Computation | Data Freshness | Scalability |
|--------|-------------------|-------------|----------------|-------------|
| **OpenRMF** | Central server | O(N robots) at server | Perfect | Bottleneck at ~50 robots |
| **Your Model** | Per-robot agent | O(1) per robot | 100-300ms stale | Scales to 100s of robots |

This is your thesis: **Decentralized pathfinding with gossiped congestion data scales better than centralized traffic coordination, at the cost of ~10-20% efficiency from stale data.**

---

## Python Libraries for Minimal Implementation

### CRDT Libraries (Conflict-Free Replicated Data Types)

**Recommended: `pycrdt` or `datashards`**

```bash
pip install pycrdt
# OR
pip install datashards
```

**Why**: Provides battle-tested CRDT implementations (GCounter, LWWMap, ORSet) so you don't write merge logic from scratch.

**What you need**:
- `GCounter` or `PNCounter`: For flow counts, jam levels (increment-only or +/-)
- `LWWMap` (Last-Write-Wins Map): For path intents, agent locations (timestamped updates)
- `ORSet` (Observed-Remove Set): For task availability (if you move tasks to data plane)

**Example**:
```python
from pycrdt import GCounter, LWWMap

class RobotAgent:
    def __init__(self, ...):
        self.local_cache = {
            "aisle_1": {
                "flow_counts": GCounter(),
                "agent_locations": LWWMap(),
                "jam_levels": LWWMap()
            }
        }
    
    def write_flow(self, node_id):
        # Increment flow count (CRDT handles conflicts)
        self.local_cache[self.get_shard(node_id)]["flow_counts"].increment(node_id)
    
    def gossip_with(self, other_agent):
        # Merge CRDTs (automatic conflict resolution)
        for shard_key in self.local_cache.keys():
            self.local_cache[shard_key]["flow_counts"].merge(
                other_agent.local_cache[shard_key]["flow_counts"]
            )
```

### Gossip Protocol Libraries

**Recommended: Implement simple epidemic protocol (15 lines)**

Don't over-engineer - gossip is just "pick 2 random agents, merge their caches."

```python
# In model.step() - every 150ms
def gossip_step(self):
    if self.step_count % self.gossip_interval != 0:
        return
    
    # Pick random pairs of agents
    agents = list(self.schedule.agents)
    random.shuffle(agents)
    
    for i in range(0, len(agents) - 1, 2):
        agent_a = agents[i]
        agent_b = agents[i + 1]
        
        # Merge their local caches (CRDT handles conflicts)
        agent_a.merge_cache_with(agent_b)
```

**Alternative: Use `aiogossip` if you want production-grade**
```bash
pip install aiogossip
```
(Overkill for simulation, but available)

### Optional: Redis Coordinator (NOT recommended for simulation)



### Recommended Minimal Stack

```python
# requirements.txt
mesa>=2.0.0           # Agent-based modeling
networkx>=3.0         # Graph operations
pycrdt>=0.1.0         # CRDTs (minimal code)
redis>=5.0.0          # Optional: for strong coordinator
numpy>=1.24.0         # Math operations
```

**Total lines you write**:
- CRDT data plane: ~30 lines (just wrapper around pycrdt)
- Gossip protocol: ~15 lines (random pair merge)
- Coordinator: ~50 lines (or use Redis: ~20 lines)
- **Total: ~100 lines for entire coordination layer**

---

## API Design

### Coordinator Interface

```python
class Coordinator:
    """ZooKeeper-like coordination service for warehouse simulation.
    
    Integrates with LF (Lingua Franca) time base via tick().
    """
    
    def __init__(self, 
                 default_lease_ttl_ms: int = 5000):
        # Control Plane: Authoritative task state
        self.task_registry = TaskRegistry()  # Strong consistency
        
        # Control Plane: Exclusive locks
        self.lease_manager = LeaseManager(default_ttl_ms=default_lease_ttl_ms)
        
        # Control Plane: Event notifications
        self.watch_manager = WatchManager()
        
        # Optional: Agent membership tracking
        self.membership = MembershipTracker()
        
        self.current_time_ms = 0  # Updated by LF tick()
        
        self.metrics = {
            'claim_attempts': 0,
            'claim_successes': 0,
            'claim_conflicts': 0,
            'lease_expirations': 0,
            'lease_renewals': 0,
            'acquire_calls': 0,
            'acquire_latency_ms': [],
            'watch_events_fired': 0,
            'resource_lock_attempts': 0,
            'resource_lock_successes': 0,
            'resource_lock_conflicts': 0,
        }
    
    # === Core API ===
    
    def tick(self, current_time_ms: int) -> None:
        """Advance coordinator logical time; expire leases; detect failures."""
        self.current_time_ms = current_time_ms
        
        # Expire stale leases
        expired = self.lease_manager.expire_leases(current_time_ms)
        self.metrics['lease_expirations'] += len(expired)
        
        # Fire events for expired leases (release notification)
        for task_id, agent_id in expired:
            self.watch_manager.fire('task_released', 
                                   {'task_id': task_id, 'agent_id': agent_id})
        
        # Check agent heartbeats (optional)
        failed_agents = self.membership.detect_failures(current_time_ms)
        if failed_agents:
            # Auto-release their leases
            for agent_id in failed_agents:
                self._release_all_leases(agent_id)
    
    # === Task Registry (Authoritative State) ===
    
    def create_task(self, location: int, task_type: str = "pick", 
                   priority: float = 1.0) -> int:
        """Create a new task in the authoritative registry.
        
        This is the single source of truth for task existence and status.
        """
        task_id = self.task_registry.create_task(
            location=location,
            task_type=task_type,
            priority=priority
        )
        
        # Fire watch event
        self.watch_manager.fire('task_created', {
            'task_id': task_id,
            'location': location,
            'task_type': task_type
        })
        
        return task_id
    
    def get_available_tasks(self, region_id: int = None, 
                          max_distance: int = None) -> List[Dict]:
        """Get available tasks, optionally filtered by region.
        
        This is a strong, consistent read - no stale data.
        """
        return self.task_registry.get_available_tasks(
            location=region_id,
            max_distance=max_distance
        )
    
    def get_task_status(self, task_id: int) -> str:
        """Get current status of a task."""
        task = self.task_registry.tasks.get(task_id)
        return task.status.value if task else None
    
    def update_task_status(self, task_id: int, status: str) -> bool:
        """Update task status (internal use by claim/complete)."""
        task = self.task_registry.tasks.get(task_id)
        if task:
            old_status = task.status
            task.status = TaskStatus(status)
            return True
        return False
    
    # === Lease Management (Exclusive Claims) ===
    
    def try_claim(self, task_id: int, agent_id: int, ttl_ms: int = None) -> bool:
        """Attempt to claim a task lease. Returns True if granted.
        
        This atomically: 1) acquires lease, 2) updates task status to 'claimed'
        """
        self.metrics['claim_attempts'] += 1
        
        # Check task exists and is available
        task = self.task_registry.tasks.get(task_id)
        if not task or task.status != TaskStatus.AVAILABLE:
            self.metrics['claim_conflicts'] += 1
            return False
        
        # Try to acquire lease
        ttl = ttl_ms or self.lease_manager.default_ttl_ms
        lease_success = self.lease_manager.try_acquire(
            resource_id=task_id,
            owner_id=agent_id,
            ttl_ms=ttl,
            current_time_ms=self.current_time_ms
        )
        
        if lease_success:
            # Update task status atomically
            self.task_registry.claim_task(task_id, agent_id)
            
            self.metrics['claim_successes'] += 1
            # Fire watch event
            self.watch_manager.fire('task_claimed', 
                                   {'task_id': task_id, 'agent_id': agent_id})
        else:
            self.metrics['claim_conflicts'] += 1
        
        return lease_success
    
    def renew_claim(self, task_id: int, agent_id: int, ttl_ms: int = None) -> bool:
        """Renew an existing lease to extend TTL."""
        ttl = ttl_ms or self.lease_manager.default_ttl_ms
        success = self.lease_manager.renew(
            resource_id=task_id,
            owner_id=agent_id,
            ttl_ms=ttl,
            current_time_ms=self.current_time_ms
        )
        if success:
            self.metrics['lease_renewals'] += 1
        return success
    
    def release_claim(self, task_id: int, agent_id: int) -> bool:
        """Explicitly release a lease (for failed/cancelled tasks)."""
        # Release lease
        success = self.lease_manager.release(
            resource_id=task_id,
            owner_id=agent_id
        )
        
        if success:
            # Revert task to available
            task = self.task_registry.tasks.get(task_id)
            if task and task.agent_id == agent_id:
                self.task_registry.fail_task(task_id, agent_id, retry=True)
            
            self.watch_manager.fire('task_released', 
                                   {'task_id': task_id, 'agent_id': agent_id})
        return success
    
    def complete_task(self, task_id: int, agent_id: int) -> bool:
        """Mark task as completed and release lease.
        
        This atomically: 1) releases lease, 2) updates status to 'completed'
        """
        # Verify lease ownership
        if self.lease_manager.get_owner(task_id) != agent_id:
            return False
        
        # Release lease
        self.lease_manager.release(task_id, agent_id)
        
        # Update task status
        success = self.task_registry.complete_task(task_id, agent_id)
        
        if success:
            self.watch_manager.fire('task_completed', 
                                   {'task_id': task_id, 'agent_id': agent_id})
        
        return success
    
    def get_lease_owner(self, task_id: int) -> Optional[int]:
        """Check who currently holds the lease for a task."""
        return self.lease_manager.get_owner(task_id)
    
    # === Physical Resource Locks (JIT) ===
    
    def acquire_lock(self, resource_id: str, agent_id: int, ttl_ms: int = None) -> bool:
        """Acquire exclusive lock on a physical resource (bin, charger, etc).
        
        Use just-in-time when agent arrives at resource location.
        Returns True if granted, False if already held by another agent.
        """
        self.metrics['resource_lock_attempts'] = self.metrics.get('resource_lock_attempts', 0) + 1
        
        ttl = ttl_ms or 30_000  # Default 30 seconds for resource locks
        success = self.lease_manager.try_acquire(
            resource_id=resource_id,
            owner_id=agent_id,
            ttl_ms=ttl,
            current_time_ms=self.current_time_ms
        )
        
        if success:
            self.metrics['resource_lock_successes'] = self.metrics.get('resource_lock_successes', 0) + 1
        else:
            self.metrics['resource_lock_conflicts'] = self.metrics.get('resource_lock_conflicts', 0) + 1
        
        return success
    
    def release_lock(self, resource_id: str, agent_id: int) -> bool:
        """Release physical resource lock."""
        return self.lease_manager.release(
            resource_id=resource_id,
            owner_id=agent_id
        )
    
    # === DSM Gossip Control (Optional) ===
    
    def force_gossip(self) -> None:
        """REMOVED: Not needed in peer-to-peer architecture.
        
        In P2P, agents read from their own local_cache (instant access).
        Gossip happens via model.gossip_round() every 150ms (no central router).
        """
        pass  # No-op
    
    # === Watch/Notifications (Event-Driven) ===
    
    def watch(self, region_id: int, event_type: str, callback) -> str:
        """Register a callback for events in a region.
        
        Supported events:
          - 'task_created'
          - 'task_claimed'
          - 'task_released'
          - 'task_completed'
        
        Returns watch handle (use for unwatch).
        """
        handle = self.watch_manager.register(
            region_id=region_id,
            event_type=event_type,
            callback=callback
        )
        return handle
    
    def unwatch(self, handle: str) -> None:
        """Unregister a watch callback."""
        self.watch_manager.unregister(handle)
    
    # === Membership (Optional) ===
    
    def register_agent(self, agent_id: int) -> None:
        """Register agent with coordinator (for failure detection)."""
        self.membership.register(agent_id, self.current_time_ms)
    
    def heartbeat(self, agent_id: int) -> None:
        """Agent heartbeat to signal liveness."""
        self.membership.heartbeat(agent_id, self.current_time_ms)
    
    def get_active_agents(self) -> List[int]:
        """Get list of currently active agents."""
        return self.membership.get_active(self.current_time_ms)
    
    # === Metrics ===
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get coordinator metrics."""
        metrics = self.metrics.copy()
        if self.metrics['acquire_latency_ms']:
            metrics['avg_acquire_latency_ms'] = sum(self.metrics['acquire_latency_ms']) / len(self.metrics['acquire_latency_ms'])
            metrics['p50_acquire_latency_ms'] = sorted(self.metrics['acquire_latency_ms'])[len(self.metrics['acquire_latency_ms']) // 2]
            metrics['p90_acquire_latency_ms'] = sorted(self.metrics['acquire_latency_ms'])[int(len(self.metrics['acquire_latency_ms']) * 0.9)]
        return metrics
```

---

## Peer-to-Peer DSM Implementation (Using CRDTs)

### Agent Local Cache Structure

```python
from pycrdt import GCounter, LWWMap, PNCounter

class RobotAgent:
    def __init__(self, ...):
        # Partition cache by logical shards (regions)
        self.local_cache = {
            "storage_west": {
                "flow_counts": GCounter(),      # Monotonic edge usage
                "jam_levels": LWWMap(),         # Current jam at nodes
                "path_intents": LWWMap(),       # (agent_id, node) -> arrival_time
                "agent_positions": LWWMap()     # agent_id -> (x, y)
            },
            "storage_east": { ... },
            "sortation": { ... }
        }
    
    def get_shard_for_node(self, node_id):
        """Map node to logical shard based on region."""
        if node_id < 100:
            return "storage_west"
        elif node_id < 200:
            return "storage_east"
        else:
            return "sortation"
```

### Gossip Protocol (Epidemic)

```python
# In WarehouseModel.step()
def gossip_round(self):
    """Merge random agent pairs every 150ms."""
    if self.step_count % self.gossip_interval != 0:
        return
    
    agents = list(self.schedule.agents)
    random.shuffle(agents)
    
    # Pair agents and merge caches
    for i in range(0, len(agents) - 1, 2):
        agent_a = agents[i]
        agent_b = agents[i + 1]
        
        # Merge all shards
        for shard_key in agent_a.local_cache.keys():
            # CRDT merge is commutative and idempotent
            agent_a.local_cache[shard_key]["flow_counts"].merge(
                agent_b.local_cache[shard_key]["flow_counts"]
            )
            agent_a.local_cache[shard_key]["jam_levels"].merge(
                agent_b.local_cache[shard_key]["jam_levels"]
            )
            
            # Symmetric merge
            agent_b.local_cache[shard_key]["flow_counts"].merge(
                agent_a.local_cache[shard_key]["flow_counts"]
            )
            agent_b.local_cache[shard_key]["jam_levels"].merge(
                agent_a.local_cache[shard_key]["jam_levels"]
            )
```

### Read/Write Operations

```python
class RobotAgent:
    def write_jam_signal(self, node_id, jam_level):
        """Write jam level to local cache (instant)."""
        shard = self.get_shard_for_node(node_id)
        self.local_cache[shard]["jam_levels"].set(
            node_id, 
            jam_level, 
            timestamp=self.model.current_time_ms
        )
    
    def read_jam_signal(self, node_id):
        """Read jam level from local cache (instant, possibly stale)."""
        shard = self.get_shard_for_node(node_id)
        return self.local_cache[shard]["jam_levels"].get(node_id, 0.0)
    
    def increment_flow(self, edge_id):
        """Increment edge flow count (CRDT counter)."""
        shard = self.get_shard_for_edge(edge_id)
        self.local_cache[shard]["flow_counts"].increment(edge_id)
    
    def read_flow(self, edge_id):
        """Read edge flow count (eventually consistent)."""
        shard = self.get_shard_for_edge(edge_id)
        return self.local_cache[shard]["flow_counts"].value(edge_id)
```

### Advantages of Peer-to-Peer

1. **No central shard servers**: Eliminates DSMRouter bottleneck
2. **True decentralization**: Each agent is autonomous
3. **Simpler architecture**: No router, no halo regions, just peer gossip
4. **Minimal code**: pycrdt handles all merge logic (~30 lines total)
5. **Realistic model**: Matches real-world distributed systems (Cassandra, Riak)

### Disadvantages (Trade-offs)

1. **No ground truth**: Can't measure "staleness" easily (no authoritative state)
2. **Debugging harder**: Can't inspect "true" state, only agent views
3. **Convergence time**: May take longer for updates to reach all agents
4. **Memory overhead**: Each agent stores full cache (100s of agents = MB per agent)

---

## Implementation Components

### 1. LeaseManager (Exclusive Locks)

```python
@dataclass
class Lease:
    resource_id: int
    owner_id: int
    granted_ms: int
    expires_ms: int

class LeaseManager:
    def __init__(self, default_ttl_ms: int = 5000):
        self.default_ttl_ms = default_ttl_ms
        self.leases: Dict[int, Lease] = {}  # resource_id -> Lease
    
    def try_acquire(self, resource_id: int, owner_id: int, ttl_ms: int, 
                    current_time_ms: int) -> bool:
        """CAS-like: grant lease if resource is free or lease expired."""
        existing = self.leases.get(resource_id)
        
        # Check if free or expired
        if existing is None or existing.expires_ms <= current_time_ms:
            self.leases[resource_id] = Lease(
                resource_id=resource_id,
                owner_id=owner_id,
                granted_ms=current_time_ms,
                expires_ms=current_time_ms + ttl_ms
            )
            return True
        
        # Already held by someone else
        return False
    
    def renew(self, resource_id: int, owner_id: int, ttl_ms: int, 
              current_time_ms: int) -> bool:
        """Extend lease TTL if owned by this agent."""
        lease = self.leases.get(resource_id)
        if lease and lease.owner_id == owner_id and lease.expires_ms > current_time_ms:
            lease.expires_ms = current_time_ms + ttl_ms
            return True
        return False
    
    def release(self, resource_id: int, owner_id: int) -> bool:
        """Release lease if owned by this agent."""
        lease = self.leases.get(resource_id)
        if lease and lease.owner_id == owner_id:
            del self.leases[resource_id]
            return True
        return False
    
    def expire_leases(self, current_time_ms: int) -> List[Tuple[int, int]]:
        """Remove expired leases; return list of (resource_id, owner_id)."""
        expired = []
        for resource_id, lease in list(self.leases.items()):
            if lease.expires_ms <= current_time_ms:
                expired.append((resource_id, lease.owner_id))
                del self.leases[resource_id]
        return expired
    
    def get_owner(self, resource_id: int) -> Optional[int]:
        """Get current owner of a resource, or None if free."""
        lease = self.leases.get(resource_id)
        return lease.owner_id if lease else None
```

### 2. WatchManager (Event Notifications)

```python
@dataclass
class Watch:
    handle: str
    region_id: int
    event_type: str
    callback: Callable

class WatchManager:
    def __init__(self):
        self.watches: Dict[str, Watch] = {}
        self.watch_counter = 0
    
    def register(self, region_id: int, event_type: str, callback) -> str:
        """Register a callback; return handle."""
        handle = f"watch_{self.watch_counter}"
        self.watch_counter += 1
        self.watches[handle] = Watch(
            handle=handle,
            region_id=region_id,
            event_type=event_type,
            callback=callback
        )
        return handle
    
    def unregister(self, handle: str) -> None:
        """Remove a watch."""
        self.watches.pop(handle, None)
    
    def fire(self, event_type: str, event_data: Dict[str, Any]) -> int:
        """Fire event to matching watches; return count fired."""
        fired = 0
        for watch in self.watches.values():
            if watch.event_type == event_type:
                # Optional: filter by region if event_data has 'region_id'
                try:
                    watch.callback(event_data)
                    fired += 1
                except Exception as e:
                    logging.error(f"Watch callback failed: {e}")
        return fired
```

---

## Integration Points

### In `world/model.py:step()`

```python
def step(self):
    """Execute one model step"""
    self.step_count += 1
    current_time_ms = int(self.step_count * self.step_duration_s * 1000)
    
    # NEW: Tick coordinator first (expire leases, detect failures)
    if hasattr(self, 'coordinator') and self.coordinator:
        self.coordinator.tick(current_time_ms)
    
    # Cleanup expired edge reservations
    expired_keys = [k for k, exp in self.edge_reservations.items() if exp <= self.step_count]
    for k in expired_keys:
        self.edge_reservations.pop(k, None)
    
    # Generate new tasks
    self._generate_tasks()
    
    # Step all agents
    self.schedule.step()
    
    # Clean up completed tasks
    self._cleanup_tasks()
    
    # Periodic halo exchange for distributed DSM (data plane)
    if hasattr(self.dsm, 'periodic_gossip'):
        self.dsm.periodic_gossip(current_time_ms)
    
    # Collect data
    self.datacollector.collect(self)
```

### In `world/agent.py:_handle_idle()`

```python
def _handle_idle(self):
    """Event-driven: Watch for tasks"""
    if not hasattr(self, 'watch_handle'):
        # Register watch on first idle
        self.watch_handle = self.model.coordinator.watch(
            region_id=self.get_region(), 
            event_type='task_created', 
            callback=self.on_new_task
        )
        # Do initial scan (in case tasks already exist)
        self.on_new_task()
    
    # Otherwise, just idle/wander (callback will trigger on new tasks)
    self._idle_wander_to_staging()

def on_new_task(self):
    """Callback: Try to claim a task when notified."""
    tasks = self.model.coordinator.get_available_tasks(region_id=self.get_region())
    if not tasks:
        return
    
    best_task = self.choose_best(tasks)  # Sort by distance, priority
    if not best_task:
        return
    
    success = self.model.coordinator.try_claim(best_task.id, self.unique_id, ttl_ms=300_000)
    if success:
        self.model.coordinator.unwatch(self.watch_handle)  # Stop watching
        self.watch_handle = None
        self.current_task_id = best_task.id
        self.task_location = best_task.location
        self.state = AgentState.NAVIGATING
        self.path = self._plan_path(self.node, self.task_location)

def _handle_working(self):
    """Execute work at task location (with coordinator)."""
    coordinator = getattr(self.model, 'coordinator', None)
    
    if self.work_timer <= 0:
        # Work complete
        if coordinator:
            # Release physical resource lock (storage/sortation node)
            node_resource_id = f"node_{self.task_location}"
            coordinator.release_lock(node_resource_id, self.unique_id)
            
            # Complete task (atomic: release lease + update status)
            coordinator.complete_task(self.current_task_id, self.unique_id)
        
        self.metrics['tasks_completed'] += 1
        self.current_task_id = None
        self.task_location = None
        self.state = AgentState.IDLE
        return
    
    self.work_timer -= 1
```

---

## Consistency Models to Compare

With the coordination layer, you can now A/B test:

| Mode | Task Reads | Task Claims | DSM Layer Reads | Use Case |
|------|-----------|-------------|-----------------|----------|
| `centralized` | Strong (single registry) | Strong (direct dict) | Perfect (single DSM) | Theoretical max |
| `dsm_eventual` | Eventual (cached merge) | Weak (per-shard CAS) | Eventual (gossip + AoI) | Current baseline |
| `coord_strong` | **Strong (coordinator)** | **Strong (lease)** | Eventual (gossip + AoI) | Strong control + eventual data |
| `coord_watch` | **Strong (coordinator)** | **Strong (lease)** | Eventual + events | Event-driven + strong control |

**Key Distinctions**:
- **Task operations** (create, read available, claim, complete): Always STRONG in coordinator
- **DSM layer operations** (jam_signal, flow_trace, path_intent): Always eventual consistency + CRDT + gossip
- **Optional**: `force_gossip()` to get fresher DSM data before pathfinding (not a different consistency model, just triggers gossip early)

### Two-Tier Consistency Model

**Control Plane (Strong Consistency)**:
- TaskRegistry in Coordinator
- All task operations are linearizable
- `get_available_tasks()` always returns current state
- `try_claim()` is atomic CAS with immediate visibility
- No stale task data ever

**Data Plane (Eventual Consistency + CRDT + Gossip)**:
- DSM layers (`jam_signal`, `flow_trace`, `path_intent`)
- CRDT merge operators for conflict-free updates
- Gossiped every 150ms (default)
- ~100-300ms staleness typical
- Optional: `force_gossip()` to trigger early (still eventual, just faster propagation)

**Why This Works**:
- Tasks need strong consistency (who owns what task?) → Coordinator
- Congestion/flow data can tolerate 100-300ms staleness → DSM gossip
- Forced gossip is optional optimization, not required for correctness
- Strong task consistency + eventual flow data = scalable + correct

---

## Metrics to Collect

Add to `WarehouseDSMModel` metrics:

```python
# Coordinator metrics (per run)
coordinator_metrics = {
    'claim_attempts': 0,
    'claim_successes': 0,
    'claim_conflicts': 0,
    'lease_expirations': 0,
    'acquire_calls': 0,
    'avg_acquire_latency_ms': 0.0,
    'watch_events_fired': 0,
}

# Compare against DSM metrics
dsm_metrics = {
    'reads': 0,
    'writes': 0,
    'gossip_messages': 0,
    'aoi_violations': 0,
    'coordination_time': 0.0,
}

# Correlation analysis
# - Does lower aoi_violations → higher throughput?
# - Does lower claim_conflicts → lower T90?
# - Does acquire_latency scale with num_shards?
```

---

## Implementation Plan

### Phase 1: Core Coordinator (1-2 days)
- [ ] Create `warehouse/coord/` module
- [ ] Implement `LeaseManager` (exclusive claims)
- [ ] Implement `Coordinator` facade with `try_claim`, `release`, `tick`
- [ ] Unit tests for lease expiry, conflict detection

### Phase 2: Integration (1 day)
- [ ] Add `coordinator` to `WarehouseDSMModel.__init__`
- [ ] Call `coordinator.tick(lf_time_ms)` in `model.step()` (LF time base)
- [ ] Wire `RobotAgent._handle_idle()` to use event-driven `coordinator.watch()`
- [ ] Collect metrics: `claim_conflicts`, `lease_expirations`

### Phase 3: Peer-to-Peer DSM with CRDTs (2 days)
- [ ] Install `pycrdt` library
- [ ] Add `local_cache` dict with CRDTs to `RobotAgent.__init__`
- [ ] Implement `gossip_round()` in `WarehouseModel.step()` (epidemic protocol)
- [ ] Replace `dsm_api.read_window()` with `self.local_cache[shard].get()`
- [ ] Replace `dsm_api.write_delta()` with `self.local_cache[shard].set()`
- [ ] **DELETE `dsm/router.py` and `DSMRouter` class** (no longer needed)
- [ ] **DELETE shard instances** (agents are the only nodes)
- [ ] Test convergence: verify all agents eventually see same data

### Phase 4: Watch/Notifications (1 day)
- [ ] Implement `WatchManager` (event callbacks)
- [ ] Fire events: `task_created`, `task_claimed`, `task_released`
- [ ] Add agent watch registration for nearby region
- [ ] Test interrupt-idle on new task event

### Phase 5: Experiments (1-2 days)
- [ ] Add config mode: `consistency_model: centralized | p2p_crdt | p2p_crdt_watch`
- [ ] Run sweeps: vary `gossip_period_ms` (50ms, 150ms, 300ms), `lease_ttl_ms`
- [ ] Plot: throughput, T90, convergence_time, claim_conflicts vs config
- [ ] Measure memory overhead: cache size per agent
- [ ] Document results: peer-to-peer vs centralized trade-offs

---

## Expected Outcomes

1. **Lower claim conflicts**: Coordinator leases (strong consistency) prevent double-claims
2. **True decentralization**: Peer-to-peer gossip with CRDTs, no central shard servers
3. **Faster local reads**: Agents read from their own cache (instant, no network)
4. **Minimal code**: `pycrdt` handles merge logic (~100 lines total for coordination layer)
5. **Cleaner separation**: 
   - **Control Plane** (Coordinator): Strong consistency for tasks/leases
   - **Data Plane** (P2P CRDTs): Eventual consistency for spatial signals
6. **Research insight**: Quantify peer-to-peer scalability vs centralized control under varying gossip rates

---

## Future Extensions

- **Multi-host simulation**: Replace in-process coordinator with Redis/etcd
- **Fault injection**: Kill agents mid-claim; test lease expiry and recovery
- **Hierarchical coordination**: Region-specific coordinators federated by zone
- **Adaptive TTLs**: Increase lease TTL under high contention
- **Priority queues**: Coordinator respects task priority for claim ordering
- **Intelligent gossip**: Gossip more frequently with spatially-close agents
- **CRDT compaction**: Periodically compact CRDT tombstones to reduce memory

---

## Final Architecture Summary

```
┌─────────────────────────────────────────────────────────────────┐
│                     WAREHOUSE SIMULATION                         │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌────────────────────── CONTROL PLANE ──────────────────────┐  │
│  │  model.coordinator (In-Process, Strong Consistency)       │  │
│  │                                                            │  │
│  │  - TaskRegistry: Authoritative task state                 │  │
│  │  - LeaseManager: Atomic task/resource locks (ZK-style)    │  │
│  │  - WatchManager: Event notifications                      │  │
│  │  - Guarantees: Linearizability (single-threaded Mesa)     │  │
│  │  - Volume: LOW (task ops: 1-10/sec)                       │  │
│  └────────────────────────────────────────────────────────────┘  │
│                              ↕                                   │
│                    (try_claim, release_claim)                    │
│                              ↕                                   │
│  ┌────────────────────── AGENTS (Peers) ─────────────────────┐  │
│  │                                                            │  │
│  │  Robot A      Robot B      Robot C      Robot D           │  │
│  │  ┌────────┐  ┌────────┐  ┌────────┐  ┌────────┐          │  │
│  │  │ Local  │  │ Local  │  │ Local  │  │ Local  │          │  │
│  │  │ A*     │  │ A*     │  │ A*     │  │ A*     │          │  │
│  │  │        │  │        │  │        │  │        │          │  │
│  │  │ CRDT   │  │ CRDT   │  │ CRDT   │  │ CRDT   │          │  │
│  │  │ Cache: │  │ Cache: │  │ Cache: │  │ Cache: │          │  │
│  │  │ - jam  │  │ - jam  │  │ - jam  │  │ - jam  │          │  │
│  │  │ - flow │  │ - flow │  │ - flow │  │ - flow │          │  │
│  │  │ - paths│  │ - paths│  │ - paths│  │ - paths│          │  │
│  │  └────────┘  └────────┘  └────────┘  └────────┘          │  │
│  │       ↕ ←───────→ ↕ ←───────→ ↕ ←───────→ ↕              │  │
│  │          (peer-to-peer gossip, 150ms rounds)              │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                  │
│  ┌────────────────────── DATA PLANE ─────────────────────────┐  │
│  │  Peer-to-Peer CRDTs (Eventual Consistency)                │  │
│  │                                                            │  │
│  │  - GCounter: Edge flow counts (monotonic)                 │  │
│  │  - LWWMap: Jam levels, path intents (timestamped)         │  │
│  │  - Gossip: Epidemic protocol (random pairs)               │  │
│  │  - Guarantees: Eventual consistency, conflict-free merge  │  │
│  │  - Volume: HIGH (spatial updates: 100s/sec)               │  │
│  │  - Staleness: 100-300ms (acceptable for pathfinding)      │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘

KEY INNOVATIONS:
1. Control/Data Plane Separation: Strong consistency for low-volume tasks,
   eventual consistency for high-volume spatial data
2. Peer-to-Peer DSM: No central shard servers, agents gossip directly
3. Distributed Pathfinding: Each agent runs local A* with (stale) CRDT data
4. Minimal Code: pycrdt library handles all merge logic (~100 lines total)
5. ZooKeeper-style Leases: Atomic task claims prevent double-claims

COMPARISON TO CENTRALIZED:
- Centralized (OpenRMF): All pathfinding at central server → bottleneck
- This model: Distributed pathfinding + gossiped data → scales to 100s of agents
- Trade-off: ~10-20% efficiency loss from stale data, but 10x scalability gain
```

---

**End of Coordination Layer Plan**

