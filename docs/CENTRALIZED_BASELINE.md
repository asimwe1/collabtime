# Centralized Baseline Implementation

## Overview

This centralized baseline models traditional warehouse systems (like OpenRMF) where a single central scheduler handles ALL pathfinding for ALL agents. This creates an architectural bottleneck that distributed P2P systems avoid.

## Architecture Comparison

### What's the SAME in Both Modes (Control Plane)
- **Task Coordination**: Both use Coordinator for task claims/leases
- **Resource Locks**: Both use JIT locks at work locations
- **Task Registry**: Authoritative task state (strong consistency)

This keeps the comparison fair - we're ONLY testing pathfinding approaches.

### What's DIFFERENT (Data Plane)

**Distributed P2P (Your Innovation)**
- **Agents**: Smart - plan own paths using local stale data
- **Data Plane**: LocalDSMCache + gossip (eventual consistency)
- **Pathfinding**: Each agent runs its own A* in parallel
- **Bottleneck**: Gossip overhead, AoI staleness
- **Scalability**: Should scale near-linearly

**Centralized Baseline (OpenRMF-style)**
- **Agents**: Dumb workers - request paths from central scheduler  
- **Data Plane**: Central scheduler has perfect global state
- **Pathfinding**: Single scheduler runs A* serially for ALL agents
- **Bottleneck**: Central scheduler CPU serialization (THE KEY DIFFERENCE)
- **Scalability**: Should hit wall at high N

## Implementation

### 1. CentralizedScheduler (`central/scheduler.py`)
- Single authority with perfect global state
- Agents serialize all path requests through it
- Maintains space-time reservations
- **This is the bottleneck being tested**
- Note: Task coordination still uses shared Coordinator (not a bottleneck)

### 2. Conditional Agent Logic (`world/agent.py`)
- `_plan_path()` - routes to centralized or distributed
- `_plan_path_centralized()` - dumb worker, asks scheduler
- `_plan_path_distributed()` - smart agent, uses local cache

### 3. Model Mode Support (`world/model.py`)
- `mode` parameter: 'p2p' or 'centralized'
- Initializes CentralizedScheduler only in centralized mode
- Disables gossip in centralized mode (no overhead)

## How to Run

### Run Centralized Baseline
```bash
python experiments/run.py -e centralized_100_agents -d 900
```

### Run Distributed (P2P)
```bash
python experiments/run.py -e scalability_100_agents -d 900
```

### Compare Results
```bash
python experiments/compare.py --run run016
```

## Expected Results

### Key Metrics

**1. Throughput (tasks/min)**
- Low N (10-50): Centralized slightly better (perfect info)
- High N (100+): Distributed should match or exceed
- Very High N (300+): Centralized should collapse

**2. Coordination Overhead**
- Centralized: Path request latency (should grow exponentially)
- Distributed: Age of Information (should grow slowly)

**3. Utilization**
- Centralized: Should decrease at high N (agents waiting for scheduler)
- Distributed: Should remain stable

## Research Value

This comparison proves your distributed architecture solves a real bottleneck. When distributed matches or exceeds centralized at scale, you've demonstrated:

1. **Scalability**: No central bottleneck
2. **Efficiency**: Parallel pathfinding works
3. **Practicality**: Eventual consistency is acceptable trade-off

The fact that centralized gets ZERO-LATENCY perfect state (unrealistic) makes your results even more impressive when P2P is competitive.

