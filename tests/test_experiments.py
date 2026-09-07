from experiments.run import ExperimentRunner


def test_runner_seed_reproduces_graph_and_agent_positions(tmp_path):
    runner = ExperimentRunner("experiments/configs.yaml", output_dir=tmp_path)
    config = runner.experiments["baseline_10_agents"]
    seed = config["simulation"]["seed"]

    first_graph = runner.create_warehouse_graph(config["warehouse"], seed=seed)
    second_graph = runner.create_warehouse_graph(config["warehouse"], seed=seed)
    first_positions = runner.generate_agent_positions(config["agents"], first_graph, seed=seed)
    second_positions = runner.generate_agent_positions(config["agents"], second_graph, seed=seed)

    assert first_graph.node_types == second_graph.node_types
    assert first_positions == second_positions
