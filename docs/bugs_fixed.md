# Critical Bugs Fixed

## Bug 1: Gossip Merge Not Sharing Agent Locations
**Fixed:** Nov 17, 2025 01:04 AM

**Problem:**
- `LocalDSMCache.merge_from()` was not correctly copying agent location entries during gossip
- Agents were "blind" to each other's positions
- `_can_move_to()` always returned True (no congestion detected)
- `stuck_counter` never incremented
- Jam signals never written
- Resulted in unrealistically low congestion metrics

**Impact:**
- run021 (300 agents distributed): INVALID - ran before fix
- run022 (500 agents distributed): INVALID - ran before fix  
- run024 (100 agents distributed): VALID - ran after fix

**Fix:**
Modified `dsm/local_cache.py` line 185 to use `.copy()` when merging entries:
```python
self.agent_location[agent_id] = other_entry.copy()
```

## Bug 2: Centralized Scheduler Not Tracking Task Assignments
**Fixed:** Nov 17, 2025 08:XX AM

**Problem:**
- Central scheduler gave paths without knowing which tasks were claimed
- Multiple agents got paths to same task location
- Created "thundering herd" - 738 attempts on single task!
- Centralized had 21.6x MORE lock contention failures (6,456 vs 299)
- Not representative of real centralized systems like OpenRMF

**Impact:**
- run023 (500 agents centralized): Had artificial thundering herd bottleneck
- ALL centralized runs before this fix: Unfair comparison

**Fix:**
Modified `central/scheduler.py` to:
1. Accept `coordinator` reference
2. Track `agent_task_assignments` dict
3. Provide `notify_task_claimed()` and `notify_task_released()` methods

Modified `world/agent.py` to notify scheduler when:
- Task claimed (line 211-216)
- Task completed (line 348-349)  
- Task failed (line 470-471)

## Invalid Experiment Data

### Must Re-run:
1. **run021**: 300 agents distributed (gossip bug)
2. **run022**: 500 agents distributed (gossip bug)
3. **run023**: 500 agents centralized (no task tracking)
4. **All centralized runs**: Need task tracking fix

### Valid Data:
1. **run024**: 100 agents distributed (after gossip fix)
2. **run015**: 100 agents distributed (if before bug introduction)
3. **run013**: 50 agents distributed (if before bug introduction)

## Next Steps
1. Re-run all experiments with both bugs fixed
2. Compare valid distributed vs centralized at: 50, 100, 300, 500 agents
3. Document scalability analysis

