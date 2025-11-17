"""
Warehouse Graph Construction and Management

Creates warehouse floor layouts as NetworkX graphs with storage and sortation regions.
Supports different warehouse topologies and pathfinding.
"""

import networkx as nx
from typing import Dict, List, Tuple, Set, Optional
import random
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))
from config import (calculate_pick_pack_locations, 
                    VERTICAL_AISLE_WIDTH, HORIZONTAL_AISLE_WIDTH,
                    MIDDLE_SEPARATOR_WIDTH,
                    SHELF_BLOCK_WIDTH, SHELF_BLOCK_HEIGHT,
                    PERIMETER_DEPTH, BUFFER_DEPTH,
                    STAGING_CAPACITY, AISLE_CAPACITY, SHELF_CAPACITY, WORK_STATION_CAPACITY,
                    DEFAULT_WAREHOUSE_WIDTH, DEFAULT_WAREHOUSE_HEIGHT)


class WarehouseGraph:
    """Manages the warehouse floor layout as a graph
    
    Layout Structure (all configurable in config.py):
    - Perimeter: staging area (depth=PERIMETER_DEPTH)
    - Buffer: main circulation aisles (depth=BUFFER_DEPTH)
    - Interior: alternating shelf blocks and aisles
      * Vertical aisles (width=VERTICAL_AISLE_WIDTH): main traffic corridors
      * Horizontal aisles (width=HORIZONTAL_AISLE_WIDTH): access to shelves
      * Shelf blocks (SHELF_BLOCK_WIDTH x SHELF_BLOCK_HEIGHT)
    - Middle: separator dividing storage/sortation (width=MIDDLE_SEPARATOR_WIDTH)
    """
    
    def __init__(self, width: int = DEFAULT_WAREHOUSE_WIDTH, 
                 height: int = DEFAULT_WAREHOUSE_HEIGHT, 
                 vertical_aisle_width: int = VERTICAL_AISLE_WIDTH,
                 horizontal_aisle_width: int = HORIZONTAL_AISLE_WIDTH,
                 middle_separator_width: int = MIDDLE_SEPARATOR_WIDTH,
                 shelf_block_width: int = SHELF_BLOCK_WIDTH, 
                 shelf_block_height: int = SHELF_BLOCK_HEIGHT,
                 perimeter_depth: int = PERIMETER_DEPTH,
                 buffer_depth: int = BUFFER_DEPTH):
        self.width = width
        self.height = height
        self.vertical_aisle_width = vertical_aisle_width
        self.horizontal_aisle_width = horizontal_aisle_width
        self.middle_separator_width = middle_separator_width
        self.shelf_block_width = shelf_block_width
        self.shelf_block_height = shelf_block_height
        self.perimeter_depth = perimeter_depth
        self.buffer_depth = buffer_depth
        
        self.graph = nx.Graph()
        self.regions = {}
        self.node_types = {}
        self.capacities = {}
        self.costs = {}
        
        self._build_warehouse()
    
    def _build_warehouse(self):
        """Build realistic warehouse with configurable aisle pattern"""
        # Perimeter staging
        perimeter_edge = self.perimeter_depth - 1
        is_perimeter = lambda x, y: (x <= perimeter_edge or x >= self.width - self.perimeter_depth or 
                                     y <= perimeter_edge or y >= self.height - self.perimeter_depth)
        
        # Aisle buffer layer (inside perimeter)
        buffer_inner = self.perimeter_depth
        buffer_outer_x = self.width - self.perimeter_depth - 1
        buffer_outer_y = self.height - self.perimeter_depth - 1
        is_buffer = lambda x, y: (x == buffer_inner or x == buffer_outer_x or 
                                  y == buffer_inner or y == buffer_outer_y)
        
        # Middle separator (divides storage/sortation)
        middle_center = self.width // 2
        middle_start = middle_center - (self.middle_separator_width // 2)
        middle_end = middle_start + self.middle_separator_width
        
        # Calculate vertical aisle columns (main traffic corridors)
        aisle_columns = set()
        start_x = self.perimeter_depth + self.buffer_depth
        pattern_size = self.shelf_block_width + self.vertical_aisle_width
        x = start_x + self.shelf_block_width
        while x < middle_start - 1:
            for offset in range(self.vertical_aisle_width):
                if x + offset < middle_start:
                    aisle_columns.add(x + offset)
            x += pattern_size
        
        # middle separator
        for col in range(middle_start, middle_end):
            aisle_columns.add(col)
        
        # Continue pattern in sortation region
        x = middle_end + self.shelf_block_width
        while x < self.width - self.perimeter_depth - self.buffer_depth:
            for offset in range(self.vertical_aisle_width):
                if x + offset < self.width - self.perimeter_depth - self.buffer_depth:
                    aisle_columns.add(x + offset)
            x += pattern_size
        
        # horizontal aisle rows
        aisle_rows = set()
        start_y = self.perimeter_depth + self.buffer_depth
        y = start_y + self.shelf_block_height
        while y < self.height - self.perimeter_depth - self.buffer_depth:
            for offset in range(self.horizontal_aisle_width):
                if y + offset < self.height - self.perimeter_depth - self.buffer_depth:
                    aisle_rows.add(y + offset)
            y += self.shelf_block_height + self.horizontal_aisle_width
        
        # Create all nodes
        for y in range(self.height):
            for x in range(self.width):
                node_id = y * self.width + x
                self.graph.add_node(node_id, x=x, y=y)
                
                # Determine node type (priority order matters)
                if is_perimeter(x, y):
                    self.node_types[node_id] = 'staging'
                    self.capacities[node_id] = STAGING_CAPACITY
                elif is_buffer(x, y):
                    self.node_types[node_id] = 'aisle'
                    self.capacities[node_id] = AISLE_CAPACITY
                elif x in aisle_columns or y in aisle_rows:
                    self.node_types[node_id] = 'aisle'
                    self.capacities[node_id] = AISLE_CAPACITY
                else:
                    self.node_types[node_id] = 'shelf'
                    self.capacities[node_id] = SHELF_CAPACITY
        
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
        num_pick, num_pack = calculate_pick_pack_locations(self.width, self.height)
        
        # exclude  middle passway from pick/pack locations
        middle_center = self.width // 2
        middle_start = middle_center - (self.middle_separator_width // 2)
        middle_end = middle_start + self.middle_separator_width
        
        # Filter out middle passway from storage aisles
        storage_nodes = []
        for n in self.regions['storage']:
            if self.node_types.get(n) == 'aisle':
                x, y = self.node_to_pos(n)
                if x < middle_start or x >= middle_end:
                    storage_nodes.append(n)
        
        if storage_nodes:
            pick_locations = random.sample(storage_nodes, min(num_pick, len(storage_nodes)))
            
            for node in pick_locations:
                self.node_types[node] = 'pick_location'
                self.capacities[node] = WORK_STATION_CAPACITY
        
        # Filter out middle passway from sortation aisles
        sortation_nodes = []
        for n in self.regions['sortation']:
            if self.node_types.get(n) == 'aisle':
                x, y = self.node_to_pos(n)
                if x < middle_start or x >= middle_end:
                    sortation_nodes.append(n)
        
        if sortation_nodes:
            pack_stations = random.sample(sortation_nodes, min(num_pack, len(sortation_nodes)))
            
            for node in pack_stations:
                self.node_types[node] = 'pack_station'
                self.capacities[node] = WORK_STATION_CAPACITY

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