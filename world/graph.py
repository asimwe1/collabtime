"""
Warehouse Graph Construction and Management

Creates warehouse floor layouts as NetworkX graphs with storage and sortation regions.
Supports different warehouse topologies and pathfinding.
"""

import networkx as nx
from typing import Dict, List, Tuple, Set, Optional
import random


class WarehouseGraph:
    """Manages the warehouse floor layout as a graph"""
    
    def __init__(self, width: int = 20, height: int = 10):
        self.width = width
        self.height = height
        self.graph = nx.Graph()
        self.regions = {}  # region_name -> set of nodes
        self.node_types = {}  # node_id -> type (storage, sortation, transit)
        self.capacities = {}  # node_id -> max_agents
        self.costs = {}  # (node1, node2) -> movement_cost
        
        self._build_warehouse()
    
    def _build_warehouse(self):
        """Build realistic warehouse with perimeter staging, aisle buffers, and wide separator"""
        # Perimeter staging (all 4 walls)
        is_perimeter = lambda x, y: (x == 0 or x == self.width-1 or y == 0 or y == self.height-1)
        
        # Aisle buffer layer (1 cell inside staging)
        is_buffer = lambda x, y: (x == 1 or x == self.width-2 or y == 1 or y == self.height-2)
        
        # Calculate middle separator (2 cells wide for clear division)
        middle_left = self.width // 2 - 1
        middle_right = self.width // 2
        
        # Vertical aisles (inside buffer zone)
        aisle_columns = {5, 9, middle_left, middle_right, 11, 15}
        
        # Horizontal aisles (inside buffer zone)
        aisle_rows = {5, 9}
        
        # Create all nodes
        for y in range(self.height):
            for x in range(self.width):
                node_id = y * self.width + x
                self.graph.add_node(node_id, x=x, y=y)
                
                # Determine node type (priority order matters)
                if is_perimeter(x, y):
                    self.node_types[node_id] = 'staging'
                    self.capacities[node_id] = 1  # One agent per cell
                elif is_buffer(x, y):
                    self.node_types[node_id] = 'aisle'
                    self.capacities[node_id] = 1  # One agent per cell
                elif x in aisle_columns or y in aisle_rows:
                    self.node_types[node_id] = 'aisle'
                    self.capacities[node_id] = 1  # One agent per cell
                else:
                    # Shelf nodes are NOT traversable
                    self.node_types[node_id] = 'shelf'
                    self.capacities[node_id] = 0
        
        # Add edges ONLY between aisle/staging nodes
        for y in range(self.height):
            for x in range(self.width):
                node_id = y * self.width + x
                node_type = self.node_types[node_id]
                
                # Only connect traversable nodes
                if node_type in ['aisle', 'staging']:
                    # Right neighbor
                    if x < self.width - 1:
                        right_neighbor = y * self.width + (x + 1)
                        if self.node_types[right_neighbor] in ['aisle', 'staging']:
                            self.graph.add_edge(node_id, right_neighbor, weight=1.0)
                            self.costs[(node_id, right_neighbor)] = 1.0
                            self.costs[(right_neighbor, node_id)] = 1.0
                    
                    # Down neighbor
                    if y < self.height - 1:
                        down_neighbor = (y + 1) * self.width + x
                        if self.node_types[down_neighbor] in ['aisle', 'staging']:
                            self.graph.add_edge(node_id, down_neighbor, weight=1.0)
                            self.costs[(node_id, down_neighbor)] = 1.0
                            self.costs[(down_neighbor, node_id)] = 1.0
        
        # Define regions
        self._define_regions()
    
    def _define_regions(self):
        """Define storage and sortation regions (without overwriting node types)"""
        # Left half: Storage region
        storage_nodes = set()
        for y in range(self.height):
            for x in range(self.width // 2):
                node_id = y * self.width + x
                storage_nodes.add(node_id)
        
        # Right half: Sortation region  
        sortation_nodes = set()
        for y in range(self.height):
            for x in range(self.width // 2, self.width):
                node_id = y * self.width + x
                sortation_nodes.add(node_id)
        
        self.regions = {
            'storage': storage_nodes,
            'sortation': sortation_nodes
        }
        
        # Add some special nodes
        self._add_special_nodes()
    
    def _add_special_nodes(self):
        """Add special-purpose nodes like docks, charging stations"""
        # Pick some storage nodes as pick locations
        storage_nodes = list(self.regions['storage'])
        pick_locations = random.sample(storage_nodes, min(6, len(storage_nodes)))
        
        for node in pick_locations:
            self.node_types[node] = 'pick_location'
            self.capacities[node] = 1  # Only one agent at pick location
        
        # Pick some sortation nodes as pack stations
        sortation_nodes = list(self.regions['sortation'])
        pack_stations = random.sample(sortation_nodes, min(5, len(sortation_nodes)))
        
        for node in pack_stations:
            self.node_types[node] = 'pack_station'
            self.capacities[node] = 1  # Only one agent at pack station

    # --- Convenience mutators used by experiment configs ---
    def node_id_from_pos(self, pos: Tuple[int, int]) -> int:
        """Convert (x, y) position to node id."""
        x, y = pos
        return y * self.width + x
    
    def node_to_pos(self, node_id: int) -> Tuple[int, int]:
        """Convert node id to (x, y) position."""
        node_data = self.graph.nodes.get(node_id)
        if node_data:
            return (node_data['x'], node_data['y'])
        # Fallback calculation
        x = node_id % self.width
        y = node_id // self.width
        return (x, y)

    def add_storage_location(self, pos: Tuple[int, int]) -> None:
        """Mark a grid cell as storage and update region maps."""
        x, y = pos
        if 0 <= x < self.width and 0 <= y < self.height:
            node_id = self.node_id_from_pos((x, y))
            if self.graph.has_node(node_id):
                # Update type
                self.node_types[node_id] = 'storage'
                # Update regions
                self.regions.setdefault('storage', set()).add(node_id)
                # Remove from sortation if previously assigned
                if 'sortation' in self.regions:
                    self.regions['sortation'].discard(node_id)

    def add_sortation_location(self, pos: Tuple[int, int]) -> None:
        """Mark a grid cell as sortation and update region maps."""
        x, y = pos
        if 0 <= x < self.width and 0 <= y < self.height:
            node_id = self.node_id_from_pos((x, y))
            if self.graph.has_node(node_id):
                # Update type
                self.node_types[node_id] = 'sortation'
                # Update regions
                self.regions.setdefault('sortation', set()).add(node_id)
                # Remove from storage if previously assigned
                if 'storage' in self.regions:
                    self.regions['storage'].discard(node_id)
    
    def get_shortest_path(self, start: int, end: int) -> List[int]:
        """Get shortest path between two nodes"""
        try:
            return nx.shortest_path(self.graph, start, end, weight='weight')
        except nx.NetworkXNoPath:
            return []
    
    def get_path_length(self, start: int, end: int) -> float:
        """Get length of shortest path"""
        try:
            return nx.shortest_path_length(self.graph, start, end, weight='weight')
        except nx.NetworkXNoPath:
            return float('inf')
    
    def get_neighbors(self, node: int) -> List[int]:
        """Get neighboring nodes"""
        return list(self.graph.neighbors(node))
    
    def get_region_nodes(self, region: str) -> Set[int]:
        """Get all nodes in a region"""
        return self.regions.get(region, set())
    
    def get_node_type(self, node: int) -> str:
        """Get the type of a node"""
        return self.node_types.get(node, 'unknown')
    
    def get_node_capacity(self, node: int) -> int:
        """Get the capacity of a node"""
        return self.capacities.get(node, 1)
    
    def get_movement_cost(self, from_node: int, to_node: int) -> float:
        """Get cost of moving between adjacent nodes"""
        return self.costs.get((from_node, to_node), 1.0)
    
    def get_random_node_in_region(self, region: str) -> Optional[int]:
        """Get a random node from a specific region"""
        region_nodes = self.regions.get(region)
        if region_nodes:
            return random.choice(list(region_nodes))
        return None
    
    def get_nodes_by_type(self, node_type: str) -> List[int]:
        """Get all nodes of a specific type"""
        return [node for node, ntype in self.node_types.items() if ntype == node_type]
    
    def is_adjacent(self, node1: int, node2: int) -> bool:
        """Check if two nodes are adjacent"""
        return self.graph.has_edge(node1, node2)
    
    def get_node_position(self, node: int) -> Tuple[int, int]:
        """Get (x, y) position of a node"""
        if node in self.graph.nodes:
            node_data = self.graph.nodes[node]
            return (node_data['x'], node_data['y'])
        return (0, 0)
    
    def visualize(self, agent_positions: Dict[int, int] = None, 
                 task_locations: List[int] = None):
        """Visualize the warehouse graph (requires matplotlib)"""
        try:
            import matplotlib.pyplot as plt
            import matplotlib.patches as patches
        except ImportError:
            print("Matplotlib not available for visualization")
            return
        
        fig, ax = plt.subplots(figsize=(15, 8))
        
        # Draw nodes by type
        for node in self.graph.nodes():
            x, y = self.get_node_position(node)
            node_type = self.get_node_type(node)
            
            if node_type == 'storage':
                color = 'lightblue'
            elif node_type == 'sortation':
                color = 'lightgreen'
            elif node_type == 'pick_location':
                color = 'blue'
            elif node_type == 'pack_station':
                color = 'green'
            else:
                color = 'lightgray'
            
            circle = plt.Circle((x, y), 0.3, color=color, alpha=0.7)
            ax.add_patch(circle)
        
        # Draw edges
        for edge in self.graph.edges():
            node1, node2 = edge
            x1, y1 = self.get_node_position(node1)
            x2, y2 = self.get_node_position(node2)
            ax.plot([x1, x2], [y1, y2], 'k-', alpha=0.3, linewidth=0.5)
        
        # Draw region boundaries
        storage_nodes = self.get_region_nodes('storage')
        if storage_nodes:
            storage_rect = patches.Rectangle(
                (-0.5, -0.5), self.width // 2, self.height,
                linewidth=2, edgecolor='blue', facecolor='none', linestyle='--'
            )
            ax.add_patch(storage_rect)
            ax.text(self.width // 4, self.height + 0.5, 'Storage Region', 
                   ha='center', fontsize=12, color='blue')
        
        sortation_rect = patches.Rectangle(
            (self.width // 2 - 0.5, -0.5), self.width // 2, self.height,
            linewidth=2, edgecolor='green', facecolor='none', linestyle='--'
        )
        ax.add_patch(sortation_rect)
        ax.text(3 * self.width // 4, self.height + 0.5, 'Sortation Region', 
               ha='center', fontsize=12, color='green')
        
        # Draw agents
        if agent_positions:
            for agent_id, node in agent_positions.items():
                x, y = self.get_node_position(node)
                agent_circle = plt.Circle((x, y), 0.2, color='red', alpha=0.8)
                ax.add_patch(agent_circle)
                ax.text(x, y, str(agent_id), ha='center', va='center', 
                       fontsize=8, color='white', weight='bold')
        
        # Draw tasks
        if task_locations:
            for location in task_locations:
                x, y = self.get_node_position(location)
                task_star = plt.scatter(x, y, marker='*', s=200, color='gold', 
                                      edgecolor='orange', linewidth=2, zorder=10)
        
        ax.set_xlim(-1, self.width)
        ax.set_ylim(-1, self.height + 1)
        ax.set_aspect('equal')
        ax.set_title('Warehouse Layout')
        ax.grid(True, alpha=0.3)
        
        # Add legend
        legend_elements = [
            plt.Circle((0, 0), 0.1, color='lightblue', label='Storage'),
            plt.Circle((0, 0), 0.1, color='lightgreen', label='Sortation'),
            plt.Circle((0, 0), 0.1, color='blue', label='Pick Location'),
            plt.Circle((0, 0), 0.1, color='green', label='Pack Station'),
        ]
        
        if agent_positions:
            legend_elements.append(plt.Circle((0, 0), 0.1, color='red', label='Agents'))
        
        if task_locations:
            legend_elements.append(plt.scatter([], [], marker='*', s=100, color='gold', 
                                             edgecolor='orange', label='Tasks'))
        
        ax.legend(handles=legend_elements, loc='upper left', bbox_to_anchor=(1, 1))
        
        plt.tight_layout()
        plt.show()


def create_standard_warehouse(width: int = 20, height: int = 10) -> WarehouseGraph:
    """Create a standard warehouse layout"""
    return WarehouseGraph(width, height)


def create_complex_warehouse() -> WarehouseGraph:
    """Create a more complex warehouse with obstacles and special features"""
    warehouse = WarehouseGraph(width=25, height=15)
    
    # Add some obstacles (remove nodes and edges)
    obstacles = [
        # Central pillar
        (12, 7), (12, 8), (13, 7), (13, 8),
        # Storage racks (create aisles)
        (3, 3), (3, 4), (3, 5),
        (7, 3), (7, 4), (7, 5),
        (11, 3), (11, 4), (11, 5),
    ]
    
    for x, y in obstacles:
        if 0 <= x < warehouse.width and 0 <= y < warehouse.height:
            node_id = y * warehouse.width + x
            if warehouse.graph.has_node(node_id):
                warehouse.graph.remove_node(node_id)
                warehouse.node_types.pop(node_id, None)
                warehouse.capacities.pop(node_id, None)
    
    # Add charging stations
    charging_stations = [(1, 1), (23, 1), (1, 13), (23, 13)]
    for x, y in charging_stations:
        if 0 <= x < warehouse.width and 0 <= y < warehouse.height:
            node_id = y * warehouse.width + x
            if warehouse.graph.has_node(node_id):
                warehouse.node_types[node_id] = 'charging_station'
                warehouse.capacities[node_id] = 3
    
    return warehouse


if __name__ == "__main__":
    # Example usage
    warehouse = create_standard_warehouse()
    print(f"Created warehouse with {warehouse.graph.number_of_nodes()} nodes")
    print(f"Storage region: {len(warehouse.get_region_nodes('storage'))} nodes")
    print(f"Sortation region: {len(warehouse.get_region_nodes('sortation'))} nodes")
    print(f"Pick locations: {len(warehouse.get_nodes_by_type('pick_location'))}")
    print(f"Pack stations: {len(warehouse.get_nodes_by_type('pack_station'))}")
    
    # Test pathfinding
    start = list(warehouse.get_region_nodes('storage'))[0]
    end = list(warehouse.get_region_nodes('sortation'))[0]
    path = warehouse.get_shortest_path(start, end)
    print(f"Path from {start} to {end}: length {len(path)}")
    
    # Visualize
    warehouse.visualize()