import time

from coord.coordinator import Coordinator
from dsm.local_cache import LocalDSMCache


def test_expired_task_lease_returns_task_to_available():
    coordinator = Coordinator(default_lease_ttl_ms=10)
    task_id = coordinator.create_task(location=1)

    assert coordinator.try_claim(task_id, agent_id=7)

    coordinator.tick(10)

    task = coordinator.task_registry.get_task(task_id)
    assert task.status.value == "available"
    assert task.agent_id is None
    assert coordinator.get_lease_owner(task_id) is None


def test_gossip_merges_newer_cache_entries():
    source = LocalDSMCache(agent_id=1)
    replica = LocalDSMCache(agent_id=2)
    timestamp_ms = int(time.time() * 1000)

    source.write_flow(node_id=12, value=3.0, timestamp_ms=timestamp_ms)
    source.write_agent_location(agent_id=1, node_id=12, timestamp_ms=timestamp_ms)
    replica.merge_from(source)

    assert replica.read_flow(node_id=12, max_aoi_ms=1_000) == 3.0
    assert replica.read_agents_at_node(node_id=12, max_aoi_ms=1_000) == 1
