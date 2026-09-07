"""
Plotly figure generation for warehouse visualization.
"""

import plotly.graph_objects as go
import sys


def create_warehouse_figure(model):
    """Create Plotly figure with warehouse visualization."""
    
    fig = go.Figure()
    
    width = model.warehouse.width
    height = model.warehouse.height
    
    # Visualize all node types
    staging_x, staging_y = [], []
    aisle_x, aisle_y = [], []
    shelf_storage_x, shelf_storage_y = [], []
    shelf_sortation_x, shelf_sortation_y = [], []
    
    for node_id in range(width * height):
        x, y = model.warehouse.node_to_pos(node_id)
        node_type = model.warehouse.node_types.get(node_id, 'transit')
        
        if node_type == 'staging':
            staging_x.append(x)
            staging_y.append(y)
        elif node_type == 'aisle':
            aisle_x.append(x)
            aisle_y.append(y)
        elif node_type == 'shelf':
            # Determine if shelf is in storage or sortation region
            if node_id in model.warehouse.get_region_nodes('storage'):
                shelf_storage_x.append(x)
                shelf_storage_y.append(y)
            elif node_id in model.warehouse.get_region_nodes('sortation'):
                shelf_sortation_x.append(x)
                shelf_sortation_y.append(y)
    
    # Staging area (yellow)
    if staging_x:
        fig.add_trace(go.Scatter(
            x=staging_x, y=staging_y,
            mode='markers',
            marker=dict(size=20, color='gold', symbol='square', opacity=0.5),
            name='Staging',
            hoverinfo='skip',
            showlegend=True
        ))
    
    # Aisles (light gray - traversable paths)
    if aisle_x:
        fig.add_trace(go.Scatter(
            x=aisle_x, y=aisle_y,
            mode='markers',
            marker=dict(size=20, color='lightgray', symbol='square', opacity=0.3),
            name='Aisle',
            hoverinfo='skip',
            showlegend=True
        ))
    
    # Storage shelves (blue)
    if shelf_storage_x:
        fig.add_trace(go.Scatter(
            x=shelf_storage_x, y=shelf_storage_y,
            mode='markers',
            marker=dict(size=20, color='dodgerblue', symbol='square', opacity=0.6),
            name='Storage Shelf',
            hoverinfo='skip',
            showlegend=True
        ))
    
    # Sortation shelves (orange/coral)
    if shelf_sortation_x:
        fig.add_trace(go.Scatter(
            x=shelf_sortation_x, y=shelf_sortation_y,
            mode='markers',
            marker=dict(size=20, color='coral', symbol='square', opacity=0.6),
            name='Sortation Shelf',
            hoverinfo='skip',
            showlegend=True
        ))
    
    available_x, available_y = [], []
    claimed_x, claimed_y = [], []
    task_registry = getattr(getattr(model, 'coordinator', None), 'task_registry', None)
    if task_registry:
        for task in task_registry.tasks.values():
            if task.status.value not in ('available', 'claimed'):
                continue

            x, y = model.warehouse.node_to_pos(task.location)
            if task.status.value == 'available':
                available_x.append(x)
                available_y.append(y)
            else:
                claimed_x.append(x)
                claimed_y.append(y)
        
    # Draw agents first, then tasks on top for visibility
    
    # Agents by state - always add all traces even if empty to keep legend stable
    idle_x, idle_y, idle_text = [], [], []
    nav_x, nav_y, nav_text = [], [], []
    work_x, work_y, work_text = [], [], []
    
    for agent in model.schedule.agents:
        x, y = model.warehouse.node_to_pos(agent.node)
        text = str(agent.unique_id)
        
        if agent.state.value == 'idle':
            idle_x.append(x)
            idle_y.append(y)
            idle_text.append(text)
        elif agent.state.value == 'navigating':
            nav_x.append(x)
            nav_y.append(y)
            nav_text.append(text)
        elif agent.state.value == 'working':
            work_x.append(x)
            work_y.append(y)
            work_text.append(text)
    
    # Always add all agent state traces (even if empty) for stable legend
    fig.add_trace(go.Scatter(
        x=idle_x if idle_x else [None], 
        y=idle_y if idle_y else [None],
        mode='markers+text',
        marker=dict(size=20, color='gray', line=dict(color='black', width=2)),
        name='Idle',
        text=idle_text,
        textfont=dict(color='white', size=10),
        showlegend=True
    ))
    
    fig.add_trace(go.Scatter(
        x=nav_x if nav_x else [None], 
        y=nav_y if nav_y else [None],
        mode='markers+text',
        marker=dict(size=20, color='royalblue', line=dict(color='black', width=2)),
        name='Navigating',
        text=nav_text,
        textfont=dict(color='white', size=10),
        showlegend=True
    ))
    
    fig.add_trace(go.Scatter(
        x=work_x if work_x else [None], 
        y=work_y if work_y else [None],
        mode='markers+text',
        marker=dict(size=20, color='limegreen', line=dict(color='black', width=2)),
        name='Working',
        text=work_text,
        textfont=dict(color='white', size=10),
        showlegend=True
    ))

    # Finally draw tasks on TOP so they are visible
    fig.add_trace(go.Scatter(
        x=available_x if available_x else [None],
        y=available_y if available_y else [None],
        mode='markers',
        marker=dict(size=10, color='#FFD700', symbol='star', line=dict(color='black', width=1)),
        name='Available Tasks',
        showlegend=True
    ))

    fig.add_trace(go.Scatter(
        x=claimed_x if claimed_x else [None],
        y=claimed_y if claimed_y else [None],
        mode='markers',
        marker=dict(size=10, color='#E74C3C', symbol='diamond', line=dict(color='black', width=1)),
        name='Claimed Tasks',
        showlegend=True
    ))
    
    # Layout
    fig.update_layout(
        title=f'Warehouse Layout (Step {model.step_count})',
        xaxis=dict(range=[-0.5, width-0.5], title='X', showgrid=True, gridcolor='lightgray'),
        yaxis=dict(range=[-0.5, height-0.5], title='Y', showgrid=True, gridcolor='lightgray', scaleanchor='x'),
        height=600,
        hovermode='closest',
        showlegend=True,
        legend=dict(x=1.02, y=1, xanchor='left', yanchor='top'),
        plot_bgcolor='white',
        uirevision='constant'
    )
    
    return fig
