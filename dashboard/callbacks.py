"""
Dash callbacks for interactivity.
"""

from dash import html, Input, Output, State, callback
from dash import dcc
import time
from .visualization import create_warehouse_figure
from .state import model, model_lock, running, create_initial_model, use_lf, auto_stop_deadline


@callback(
    Output('warehouse-graph', 'figure'),
    Output('metrics', 'children'),
    Output('agent-table', 'children'),
    Output('tasks-timeseries', 'figure'),
    Output('throughput-timeseries', 'figure'),
    Output('latency-timeseries', 'figure'),
    Output('ts-store', 'data'),
    Output('run-status', 'children'),
    Output('run-status', 'style'),
    Input('interval-component', 'n_intervals'),
    Input('start-btn', 'n_clicks'),
    Input('stop-btn', 'n_clicks'),
    Input('step-btn', 'n_clicks'),
    Input('reset-btn', 'n_clicks'),
    State('ts-store', 'data'),
    prevent_initial_call=False
)
def update_visualization(n_intervals, start_clicks, stop_clicks, step_clicks, reset_clicks, ts_store):
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
        
        # Step duration depends on LF vs internal timing
        step_dt = 0.05 if use_lf.get() else 0.5
        throughput = 0.0
        if m.step_count > 0 and step_dt > 0:
            throughput = len(m.completed_tasks) / (m.step_count * step_dt)
        
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

        # Update time series store
        if not ts_store:
            ts_store = {'t': [], 'tasks_created': [], 'tasks_completed': [], 'active_tasks': [], 'throughput': [], 'latency': []}

        # Use simulated time in seconds
        t_sec = m.step_count * step_dt
        ts_store['t'].append(t_sec)
        ts_store['tasks_created'].append(m.task_counter)
        ts_store['tasks_completed'].append(len(m.completed_tasks))
        ts_store['active_tasks'].append(len(m.active_tasks))
        ts_store['throughput'].append(throughput)
        # Append latency sample if available
        if getattr(m, 'completed_latencies', None):
            # Use rolling average over last 50 completions
            recent = m.completed_latencies[-50:]
            avg_lat = sum(recent) / len(recent) if recent else 0.0
            ts_store['latency'].append(avg_lat)
        else:
            ts_store['latency'].append(0.0)

        # Limit history for performance
        max_points = 1000
        for k in ts_store:
            ts_store[k] = ts_store[k][-max_points:]

        # Build figures
        tasks_fig = {
            'data': [
                {'x': ts_store['t'], 'y': ts_store['tasks_created'], 'type': 'line', 'name': 'Created'},
                {'x': ts_store['t'], 'y': ts_store['tasks_completed'], 'type': 'line', 'name': 'Completed'},
                {'x': ts_store['t'], 'y': ts_store['active_tasks'], 'type': 'line', 'name': 'Active'},
            ],
            'layout': {'margin': {'l': 40, 'r': 20, 't': 10, 'b': 40}, 'legend': {'orientation': 'h'}}
        }

        throughput_fig = {
            'data': [
                {'x': ts_store['t'], 'y': ts_store['throughput'], 'type': 'line', 'name': 'Throughput (tasks/s)'}
            ],
            'layout': {'margin': {'l': 40, 'r': 20, 't': 10, 'b': 40}, 'legend': {'orientation': 'h'}}
        }

        latency_fig = {
            'data': [
                {'x': ts_store['t'], 'y': ts_store['latency'], 'type': 'line', 'name': 'Avg Latency (s, last 50)'}
            ],
            'layout': {'margin': {'l': 40, 'r': 20, 't': 10, 'b': 40}, 'legend': {'orientation': 'h'}}
        }

        # Status indicator
        is_running = running.get()
        status_text = 'Running' if is_running else 'Stopped'
        status_style = {'padding': '6px 10px', 'borderRadius': '6px', 
                        'backgroundColor': '#d1fae5' if is_running else '#fee2e2',
                        'color': '#065f46' if is_running else '#991b1b'}

        return fig, metrics, agent_table, tasks_fig, throughput_fig, latency_fig, ts_store, status_text, status_style


@callback(
    Output('start-btn', 'n_clicks', allow_duplicate=True),
    Output('run-status', 'children', allow_duplicate=True),
    Output('run-status', 'style', allow_duplicate=True),
    Input('start-btn', 'n_clicks'),
    State('run-status', 'children'),
    prevent_initial_call=True
)
def start_simulation(n_clicks, status_text):
    running.set(True)
    # Set auto-stop to 15 minutes from now
    auto_stop_deadline.set(time.time() + 15 * 60)
    status_style = {'padding': '6px 10px', 'borderRadius': '6px', 'backgroundColor': '#d1fae5', 'color': '#065f46'}
    return n_clicks, 'Running', status_style


@callback(
    Output('stop-btn', 'n_clicks', allow_duplicate=True),
    Output('run-status', 'children', allow_duplicate=True),
    Output('run-status', 'style', allow_duplicate=True),
    Input('stop-btn', 'n_clicks'),
    prevent_initial_call=True
)
def stop_simulation_sync(stop_clicks):
    # Only react to stop button in this callback
    running.set(False)
    auto_stop_deadline.set(None)
    status_style = {'padding': '6px 10px', 'borderRadius': '6px', 'backgroundColor': '#fee2e2', 'color': '#991b1b'}
    return stop_clicks, 'Stopped', status_style


@callback(
    Output('step-btn', 'n_clicks', allow_duplicate=True),
    Output('run-status', 'children', allow_duplicate=True),
    Output('run-status', 'style', allow_duplicate=True),
    Input('step-btn', 'n_clicks'),
    prevent_initial_call=True
)
def step_simulation(n_clicks):
    m = model.get()
    if m:
        with model_lock:
            m.step()
    status_style = {'padding': '6px 10px', 'borderRadius': '6px', 'backgroundColor': '#fef3c7', 'color': '#92400e'}
    return n_clicks, 'Stepped', status_style


@callback(
    Output('reset-btn', 'n_clicks', allow_duplicate=True),
    Output('run-status', 'children', allow_duplicate=True),
    Output('run-status', 'style', allow_duplicate=True),
    Output('ts-store', 'data', allow_duplicate=True),
    Input('reset-btn', 'n_clicks'),
    State('n-agents-slider', 'value'),
    State('width-slider', 'value'),
    State('height-slider', 'value'),
    State('task-rate-slider', 'value'),
    prevent_initial_call=True
)
def reset_simulation(n_clicks, n_agents_val, width_val, height_val, task_rate_val):
    running.set(False)
    auto_stop_deadline.set(None)
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
    # Clear plots/store and set status
    empty_ts = {'t': [], 'tasks_created': [], 'tasks_completed': [], 'active_tasks': [], 'throughput': [], 'latency': []}
    status_style = {'padding': '6px 10px', 'borderRadius': '6px', 'backgroundColor': '#eee'}
    return n_clicks, 'Stopped', status_style, empty_ts


# Removed duplicate reset callback that also wrote to ts-store to avoid Dash duplicate outputs error

