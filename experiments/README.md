# Warehouse DSM Experiments

This directory contains experiment configurations and runners for testing the warehouse distributed shared memory system.

## Files

- `configs.yaml` - Experiment configurations with different scenarios
- `run.py` - Automated experiment runner with data collection and analysis
- `results/` - Output directory for experiment results (created automatically)

## Experiment Configurations

### Available Experiments

1. **baseline_small** - Small 10x10 warehouse with 4 agents for validation
2. **scaling_medium** - Medium 20x15 warehouse with 8 agents for scalability testing  
3. **stress_high_load** - Large 25x20 warehouse with 12 agents and high task arrival rate
4. **fault_tolerance** - Tests system resilience with scheduled agent failures
5. **network_latency** - Tests performance with simulated network delays

### Configuration Structure

Each experiment defines:
- Warehouse topology (size, storage/sortation regions)
- Agent configuration (count, initial positions, failure schedules)
- DSM parameters (partition strategy, memory owners, halo radius, AoI thresholds)
- Task generation (arrival rates, types, priorities)
- Simulation settings (duration, step intervals)

## Running Experiments

### Basic Usage

```bash
# Run all experiments
python run.py

# Run specific experiments
python run.py -e baseline_small scaling_medium

# Use custom config file
python run.py -c my_configs.yaml

# Set custom output directory
python run.py -o my_results/

# Enable verbose logging
python run.py -v
```

### Advanced Options

```bash
# Run experiments in parallel (future feature)
python run.py -p

# Get help
python run.py --help
```

## Output

The experiment runner generates:

### Results Directory Structure
```
results/
├── experiment_summary_YYYYMMDD_HHMMSS.csv    # Summary metrics
├── experiment_details_YYYYMMDD_HHMMSS.json   # Detailed results
├── task_completion_timeline_YYYYMMDD_HHMMSS.png
├── performance_comparison_YYYYMMDD_HHMMSS.png
└── experiments.log                           # Execution logs
```

### Metrics Collected

**Performance Metrics:**
- Task completion rates and times
- Agent utilization and total distance traveled
- System throughput

**DSM Metrics:**
- Memory read/write operations
- Gossip message overhead
- Age of Information (AoI) violations
- Partition quality metrics

**Time Series Data:**
- Task completion timeline
- DSM operation rates
- System resource usage

## Example Configuration

```yaml
baseline_small:
  name: "Baseline Small"
  warehouse:
    size: [10, 10]
    storage_regions:
      - {center: [2, 2], radius: 2}
    sortation_regions:
      - {center: [5, 1], radius: 1}
  agents:
    count: 4
    initial_positions: [[1, 1], [8, 8], [1, 8], [8, 1]]
  dsm:
    partition_strategy: "spatial"
    memory_owners: 2
    halo_radius: 2
    aoi_threshold: 1000
  tasks:
    arrival_rate: 0.2
    types: ["pickup", "delivery", "sort"]
  simulation:
    duration: 300
    step_interval: 50
```

## Customizing Experiments

1. **Add New Experiments**: Define new configurations in `configs.yaml`
2. **Modify Metrics**: Update the metrics collection in `run.py`
3. **Custom Analysis**: Extend the plotting and analysis functions
4. **Parallel Execution**: Implement multiprocessing for faster execution

## Dependencies

The experiment runner requires:
- PyYAML for configuration parsing
- pandas for data analysis
- matplotlib for visualization
- numpy for numerical operations
- The warehouse DSM system modules (world/, dsm/, lf/)

## Integration with Lingua Franca

The experiments use the Mesa simulation framework but can be extended to integrate with Lingua Franca timing coordination:

1. **LF Reactor Integration**: Use dsm_coordinator.lf for deterministic timing
2. **Federated Execution**: Run memory owners and agents as separate federates  
3. **Real-time Scheduling**: Enable logical time progression with physical time
4. **Cross-platform Testing**: Test distributed execution across multiple nodes