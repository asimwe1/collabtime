"""
Dash callbacks for interactivity.
"""

from dash import html, Input, Output, State, callback
from .visualization import create_warehouse_figure
from .state import model, model_lock, running, create_initial_model


@callback(
    Output('warehouse-graph', 'figure'),
    Output('metrics', 'children'),
    Output('agent-table', 'children'),
    Input('interval-component', 'n_intervals'),
    Input('start-btn', 'n_clicks'),
    Input('stop-btn', 'n_clicks'),
    Input('step-btn', 'n_clicks'),
    Input('reset-btn', 'n_clicks'),
    prevent_initial_call=False
)
def update_visualization(n_intervals, start_clicks, stop_clicks, step_clicks, reset_clicks):
    """Update all visualization components."""
    with model_lock:
        m = model.get()
        if m is None:
            m = create_initial_model()
            model.set(m)
        
        fig = create_warehouse_figure(m)
        
        # Metrics
        states = {}
        for a in m.schedule.agents:
            states[a.state.value] = states.get(a.state.value, 0) + 1
        
        throughput = 0
        if m.step_count > 0:
            throughput = len(m.completed_tasks) / (m.step_count * 0.05)
        
        # Stable Agent States block: always show all states, even if zero
        all_states = ['idle', 'navigating', 'working']
        state_lines = []
        for s in all_states:
            state_lines.append(html.P(f"  {s}: {states.get(s, 0)}"))

        metrics = html.Div([
            html.P(f"Step: {m.step_count}", style={'fontWeight': 'bold'}),
            html.P(f"Tasks Created: {m.task_counter}"),
            html.P(f"Tasks Completed: {len(m.completed_tasks)}"),
            html.P(f"Active Tasks: {len(m.active_tasks)}"),
            html.P(f"Failed Tasks: {len(m.failed_tasks)}"),
            html.P(f"Throughput: {throughput:.2f} tasks/s"),
            html.Hr(),
            html.P("Agent States:", style={'fontWeight': 'bold'}),
            *state_lines,
        ])
        
        # Agent table
        table_header = [
            html.Thead(html.Tr([
                html.Th("ID"), html.Th("State"), html.Th("Position"), 
                html.Th("Task"), html.Th("Completed"), html.Th("Claimed"), 
                html.Th("Failed"), html.Th("Distance")
            ]))
        ]
        
        table_rows = []
        for agent in sorted(m.schedule.agents, key=lambda a: a.unique_id):
            x, y = m.warehouse.node_to_pos(agent.node)
            table_rows.append(html.Tr([
                html.Td(agent.unique_id),
                html.Td(agent.state.value),
                html.Td(f"({x},{y})"),
                html.Td(agent.current_task_id if agent.current_task_id else "-"),
                html.Td(agent.metrics['tasks_completed']),
                html.Td(agent.metrics['tasks_claimed']),
                html.Td(agent.metrics['tasks_failed']),
                html.Td(f"{agent.metrics['total_distance']:.1f}"),
            ]))
        
        table_body = [html.Tbody(table_rows)]
        
        # Table with proper styling
        table_style = {
            'width': '100%',
            'borderCollapse': 'collapse',
            'border': '1px solid #ddd',
            'textAlign': 'center'
        }
        
        cell_style = {
            'border': '1px solid #ddd',
            'padding': '8px',
            'textAlign': 'center'
        }
        
        header_style = {
            'border': '1px solid #ddd',
            'padding': '8px',
            'backgroundColor': '#f0f0f0',
            'fontWeight': 'bold',
            'textAlign': 'center'
        }
        
        # Apply styles to headers and cells
        for row in table_header[0].children.children:
            row.style = header_style
        
        for row in table_rows:
            for cell in row.children:
                cell.style = cell_style
        
        agent_table = html.Table(
            table_header + table_body,
            style=table_style
        )
        
        return fig, metrics, agent_table


@callback(
    Output('start-btn', 'n_clicks', allow_duplicate=True),
    Input('start-btn', 'n_clicks'),
    prevent_initial_call=True
)
def start_simulation(n_clicks):
    running.set(True)
    return n_clicks


@callback(
    Output('stop-btn', 'n_clicks', allow_duplicate=True),
    Input('stop-btn', 'n_clicks'),
    prevent_initial_call=True
)
def stop_simulation(n_clicks):
    running.set(False)
    return n_clicks


@callback(
    Output('step-btn', 'n_clicks', allow_duplicate=True),
    Input('step-btn', 'n_clicks'),
    prevent_initial_call=True
)
def step_simulation(n_clicks):
    m = model.get()
    if m:
        with model_lock:
            m.step()
    return n_clicks


@callback(
    Output('reset-btn', 'n_clicks', allow_duplicate=True),
    Input('reset-btn', 'n_clicks'),
    State('n-agents-slider', 'value'),
    State('width-slider', 'value'),
    State('height-slider', 'value'),
    State('task-rate-slider', 'value'),
    prevent_initial_call=True
)
def reset_simulation(n_clicks, n_agents_val, width_val, height_val, task_rate_val):
    running.set(False)
    with model_lock:
        # Reset DSM first so old tasks disappear
        try:
            if hasattr(model.get(), 'dsm') and model.get().dsm is not None:
                model.get().dsm.reset()
            else:
                from dsm.api import dsm as GLOBAL_DSM
                GLOBAL_DSM.reset()
        except Exception:
            pass
        
        m = create_initial_model(n_agents_val, width_val, height_val, task_rate_val)
        model.set(m)
    return n_clicks

