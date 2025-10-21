"""
Graph Partitioning and Tile Assignment

Implements Voronoi partitioning to assign warehouse graph nodes to memory owners.
Handles boundary detection and halo region calculation.
"""

from typing import Dict, List, Set, Tuple
import networkx as nx


class WarehousePartition:
    """Partitions warehouse graph into regions managed by different memory owners"""
    
    def __init__(self, graph: nx.Graph):
        self.graph = graph
        self.owner_assignments = {}  # node_id -> owner_id
        self.owner_regions = {}  # owner_id -> Set[node_id]
        self.boundary_nodes = {}  # owner_id -> Set[node_id] (boundary of their region)
        self.halo_regions = {}  # owner_id -> Set[node_id] (includes neighbor boundaries)
    
    def voronoi_partition(self, seeds: Dict[int, int]) -> Dict[int, Set[int]]:
        """
        Partition graph using Voronoi cells from seed nodes
        
        Args:
            seeds: Dict mapping owner_id -> seed_node_id
            
        Returns:
            Dict mapping owner_id -> Set of managed nodes
        """
        # Initialize distances and assignments
        distances = {node: float('inf') for node in self.graph.nodes}
        assignments = {}
        
        # Set seed distances to 0
        queue = []
        for owner_id, seed_node in seeds.items():
            distances[seed_node] = 0
            assignments[seed_node] = owner_id
            queue.append((0, seed_node, owner_id))
        
        # Dijkstra-like expansion
        queue.sort()
        
        while queue:
            dist, node, owner_id = queue.pop(0)
            
            if dist > distances[node]:
                continue
            
            # Expand to neighbors
            for neighbor in self.graph.neighbors(node):
                edge_weight = self.graph[node][neighbor].get('weight', 1.0)
                new_dist = dist + edge_weight
                
                if new_dist < distances[neighbor]:
                    distances[neighbor] = new_dist
                    assignments[neighbor] = owner_id
                    queue.append((new_dist, neighbor, owner_id))
                    queue.sort()
        
        # Group nodes by owner
        regions = {}
        for node, owner_id in assignments.items():
            if owner_id not in regions:
                regions[owner_id] = set()
            regions[owner_id].add(node)
        
        # Update internal state
        self.owner_assignments = assignments
        self.owner_regions = regions
        self._compute_boundaries()
        self._compute_halos()
        
        return regions
    
    def get_owner(self, node_id: int) -> int:
        """Get the owner ID for a specific node"""
        return self.owner_assignments.get(node_id, -1)
    
    def get_region(self, owner_id: int) -> Set[int]:
        """Get all nodes managed by an owner"""
        return self.owner_regions.get(owner_id, set())
    
    def get_boundary_nodes(self, owner_id: int) -> Set[int]:
        """Get boundary nodes for an owner's region"""
        return self.boundary_nodes.get(owner_id, set())
    
    def get_halo_region(self, owner_id: int) -> Set[int]:
        """Get halo region (own + neighbor boundaries) for an owner"""
        return self.halo_regions.get(owner_id, set())
    
    def get_neighbor_owners(self, owner_id: int) -> Set[int]:
        """Get IDs of neighboring owners"""
        if owner_id not in self.owner_regions:
            return set()
        
        neighbors = set()
        for node in self.boundary_nodes.get(owner_id, set()):
            for neighbor_node in self.graph.neighbors(node):
                neighbor_owner = self.owner_assignments.get(neighbor_node)
                if neighbor_owner is not None and neighbor_owner != owner_id:
                    neighbors.add(neighbor_owner)
        
        return neighbors
    
    def _compute_boundaries(self):
        """Compute boundary nodes for each owner's region"""
        self.boundary_nodes = {}
        
        for owner_id, region in self.owner_regions.items():
            boundary = set()
            
            for node in region:
                # Check if node has neighbors in other regions
                for neighbor in self.graph.neighbors(node):
                    neighbor_owner = self.owner_assignments.get(neighbor)
                    if neighbor_owner != owner_id:
                        boundary.add(node)
                        break
            
            self.boundary_nodes[owner_id] = boundary
    
    def _compute_halos(self, halo_depth: int = 1):
        """Compute halo regions including neighbor boundaries"""
        self.halo_regions = {}
        
        for owner_id, region in self.owner_regions.items():
            halo = region.copy()  # Start with own region
            
            # Add neighbor boundary nodes within halo_depth
            current_boundary = self.boundary_nodes.get(owner_id, set())
            
            for depth in range(halo_depth):
                next_boundary = set()
                
                for boundary_node in current_boundary:
                    for neighbor in self.graph.neighbors(boundary_node):
                        neighbor_owner = self.owner_assignments.get(neighbor)
                        if neighbor_owner != owner_id:
                            halo.add(neighbor)
                            next_boundary.add(neighbor)
                
                current_boundary = next_boundary
            
            self.halo_regions[owner_id] = halo
    
    def visualize_partition(self, node_positions: Dict[int, Tuple[float, float]] = None):
        """Create a visualization of the partition (requires matplotlib)"""
        try:
            import matplotlib.pyplot as plt
            import matplotlib.colors as mcolors
        except ImportError:
            print("Matplotlib not available for visualization")
            return
        
        if node_positions is None:
            node_positions = nx.spring_layout(self.graph)
        
        # Create color map for owners
        owners = list(self.owner_regions.keys())
        colors = list(mcolors.TABLEAU_COLORS.values())[:len(owners)]
        color_map = {owner: colors[i % len(colors)] for i, owner in enumerate(owners)}
        
        plt.figure(figsize=(12, 8))
        
        # Draw edges
        nx.draw_networkx_edges(self.graph, node_positions, alpha=0.3, width=0.5)
        
        # Draw nodes by owner
        for owner_id, region in self.owner_regions.items():
            node_list = list(region)
            nx.draw_networkx_nodes(
                self.graph, node_positions,
                nodelist=node_list,
                node_color=color_map[owner_id],
                node_size=100,
                alpha=0.7,
                label=f'Owner {owner_id}'
            )
        
        # Highlight boundary nodes
        for owner_id, boundaries in self.boundary_nodes.items():
            if boundaries:
                nx.draw_networkx_nodes(
                    self.graph, node_positions,
                    nodelist=list(boundaries),
                    node_color=color_map[owner_id],
                    node_size=200,
                    alpha=1.0,
                    edgecolors='black',
                    linewidths=2
                )
        
        plt.title("Warehouse Graph Partition")
        plt.legend()
        plt.axis('off')
        plt.tight_layout()
        plt.show()


def create_example_partition(num_owners: int = 2) -> Tuple[nx.Graph, WarehousePartition]:
    """Create an example warehouse graph with partition"""
    
    # Create a simple warehouse graph (grid-like with some irregularities)
    G = nx.grid_2d_graph(10, 20)  # 10x20 grid
    
    # Convert to simple node IDs
    mapping = {node: i for i, node in enumerate(G.nodes())}
    G = nx.relabel_nodes(G, mapping)
    
    # Add some weights to edges (representing distances/costs)
    for u, v in G.edges():
        G[u][v]['weight'] = 1.0  # Uniform weights for simplicity
    
    # Create partition
    partition = WarehousePartition(G)
    
    # Choose seed nodes (roughly evenly spaced)
    nodes = list(G.nodes())
    num_nodes = len(nodes)
    seeds = {}
    
    for i in range(num_owners):
        seed_idx = (i * num_nodes) // num_owners
        seeds[i] = nodes[seed_idx]
    
    # Perform Voronoi partitioning
    regions = partition.voronoi_partition(seeds)
    
    print(f"Created partition with {num_owners} owners:")
    for owner_id, region in regions.items():
        boundary_count = len(partition.get_boundary_nodes(owner_id))
        halo_count = len(partition.get_halo_region(owner_id))
        neighbors = partition.get_neighbor_owners(owner_id)
        
        print(f"  Owner {owner_id}: {len(region)} nodes, "
              f"{boundary_count} boundary, {halo_count} halo, "
              f"neighbors: {neighbors}")
    
    return G, partition


if __name__ == "__main__":
    # Example usage
    graph, partition = create_example_partition(num_owners=2)
    
    # Visualize (if matplotlib available)
    partition.visualize_partition()