# Performance Optimization Strategy for 1000-Agent Simulation

## Problem
Python's GIL limits throughput for 1000 agents. Current bottlenecks:
1. Gossip rounds (O(N) agent-pair merges every 3 steps)
2. A* pathfinding with DSM reads (called frequently)
3. System time calls (time.time() every agent step)
4. Dictionary operations in LocalDSMCache
5. Logging overhead

## Optimization Tiers (Fastest → Slowest to Implement)

### Tier 1: Low-Hanging Fruit (30-50% speedup, <1 hour)

#### 1.1 Replace time.time() with simulation clock
Current: `time.time()` = expensive syscall
Fix: Use `model.step_count * model.step_duration_s * 1000` everywhere
Files: agent.py (line 95), model.py, dsm/local_cache.py

#### 1.2 Reduce gossip frequency
Current: Every 3 steps (150ms) → 2000 gossip rounds per run
Proposal: Every 5-10 steps (250-500ms) → 600-1200 rounds
Trade-off: Slightly higher AoI, but still < 1s threshold
File: model.py line 220

#### 1.3 Disable verbose logging
Current: logger.info() calls with string formatting
Fix: Set log level to WARNING in production runs
Impact: 10-20% speedup for I/O-bound operations

#### 1.4 Cache pathfinding results
Current: A* recalculates from scratch every time
Fix: Cache paths for (start, goal) pairs with TTL
Impact: 20-30% reduction in pathfinding calls

### Tier 2: Data Structure Optimizations (50-100% speedup, 2-4 hours)

#### 2.1 Use NumPy arrays for spatial queries
Current: Python dicts for agent locations
Fix: NumPy array indexed by agent_id → O(1) vs O(log N)
Files: dsm/local_cache.py

#### 2.2 Spatial indexing for agent lookups
Current: Linear scan through all agents
Fix: Grid-based spatial hash (e.g., 10x10 cells)
Files: model.py, agent.py

#### 2.3 Preallocate data structures
Current: Dynamic dict resizing
Fix: Preallocate fixed-size arrays/dicts with known capacity

### Tier 3: Algorithmic Changes (100-200% speedup, 4-8 hours)

#### 3.1 Lazy gossip (only when needed)
Current: Unconditional merge every 3 steps
Fix: Gossip only when agent enters congested region
Impact: 50-70% reduction in gossip operations

#### 3.2 Hierarchical pathfinding
Current: A* on full graph every time
Fix: Precompute high-level corridors, cache subpaths
Impact: 2-3x faster pathfinding

#### 3.3 Incremental A* (reuse previous search)
Current: Full A* from scratch
Fix: D* Lite or similar for path repair
Impact: 3-5x faster replanning

### Tier 4: Parallelization (2-4x speedup, 1-2 days)

#### 4.1 Parallel gossip with multiprocessing
Problem: GIL blocks concurrent gossip
Fix: Use multiprocessing.Pool to merge caches in parallel
Caveat: Serialization overhead, careful synchronization

#### 4.2 Batch agent updates
Current: Sequential agent.step()
Fix: Group agents by region, process regions in parallel
Caveat: Need thread-safe coordinator operations

### Tier 5: Cython/C++ Hot Paths (5-10x speedup, 3-5 days)

#### 5.1 Cythonize A* pathfinding
Compile pathfinder/routing.py with Cython
Keep Python interface, get C speed

#### 5.2 Cythonize LocalDSMCache.merge_from
Most expensive operation in gossip

## Recommended Immediate Actions

For 1000-agent runs this week:

**Option A: Quick wins (1-2 hours)**
1. Replace time.time() with simulation clock
2. Reduce gossip to every 10 steps
3. Set logging to WARNING
4. Cache pathfinding results

Expected: 2-3x speedup → 1000 agents in 1-2 hours instead of 6+

**Option B: Medium effort (4-6 hours)**
Do Option A + NumPy arrays + spatial indexing
Expected: 4-5x speedup → 1000 agents in <1 hour

**Option C: Full rewrite to C++ (1-2 weeks)**
Only if you need 100+ runs at 1000+ agents
Not recommended for current timeline

## Testing Strategy

1. Profile current code: `python -m cProfile experiments/run.py`
2. Implement Tier 1 optimizations
3. Re-profile to verify gains
4. Repeat for Tier 2 if needed

## Trade-offs

- Gossip reduction → Higher AoI (but still valid for thesis)
- Caching → Memory usage increase (negligible for 1000 agents)
- Multiprocessing → Complexity (only if Tier 1-3 insufficient)

