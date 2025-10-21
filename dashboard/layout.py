"""
Dash layout components.
"""

from dash import html, dcc


def create_layout():
    """Create the main Dash layout."""
    return html.Div([
        html.H1("Warehouse DSM Simulation", style={'textAlign': 'center'}),
        
        # Controls
        html.Div([
            html.Button('Start', id='start-btn', n_clicks=0, 
                       style={'margin': '10px', 'padding': '10px 20px', 'fontSize': '16px'}),
            html.Button('Stop', id='stop-btn', n_clicks=0, 
                       style={'margin': '10px', 'padding': '10px 20px', 'fontSize': '16px'}),
            html.Button('Step', id='step-btn', n_clicks=0, 
                       style={'margin': '10px', 'padding': '10px 20px', 'fontSize': '16px'}),
            html.Button('Reset', id='reset-btn', n_clicks=0, 
                       style={'margin': '10px', 'padding': '10px 20px', 'fontSize': '16px'}),
        ], style={'textAlign': 'center', 'padding': '20px'}),
        
        # Parameters
        html.Div([
            html.H3("Parameters (adjust and click Reset)", style={'textAlign': 'center'}),
            html.Div([
                html.Div([
                    html.Label('Number of Robots:', style={'fontWeight': 'bold'}),
                    dcc.Slider(id='n-agents-slider', min=1, max=32, step=1, value=8, 
                              marks={i: str(i) for i in [1, 8, 16, 24, 32]},
                              tooltip={"placement": "bottom", "always_visible": True}),
                ], style={'width': '45%', 'display': 'inline-block', 'padding': '10px'}),
                
                html.Div([
                    html.Label('Task Arrival Rate:', style={'fontWeight': 'bold'}),
                    dcc.Slider(id='task-rate-slider', min=0.0, max=1.0, step=0.05, value=0.1,
                              marks={i/10: f'{i/10:.1f}' for i in range(0, 11, 2)},
                              tooltip={"placement": "bottom", "always_visible": True}),
                ], style={'width': '45%', 'display': 'inline-block', 'padding': '10px'}),
            ]),
            
            html.Div([
                html.Div([
                    html.Label('Warehouse Width:', style={'fontWeight': 'bold'}),
                    dcc.Slider(id='width-slider', min=5, max=50, step=5, value=20,
                              marks={i: str(i) for i in range(5, 51, 10)},
                              tooltip={"placement": "bottom", "always_visible": True}),
                ], style={'width': '45%', 'display': 'inline-block', 'padding': '10px'}),
                
                html.Div([
                    html.Label('Warehouse Height:', style={'fontWeight': 'bold'}),
                    dcc.Slider(id='height-slider', min=5, max=50, step=5, value=15,
                              marks={i: str(i) for i in range(5, 51, 10)},
                              tooltip={"placement": "bottom", "always_visible": True}),
                ], style={'width': '45%', 'display': 'inline-block', 'padding': '10px'}),
            ]),
        ], style={'backgroundColor': '#f0f0f0', 'padding': '20px', 'margin': '20px', 'borderRadius': '10px'}),
        
        # Metrics and Visualization
        html.Div([
            html.Div([
                html.H3("Live Metrics"),
                html.Div(id='metrics', style={'fontSize': '16px'}),
            ], style={'width': '30%', 'display': 'inline-block', 'verticalAlign': 'top', 'padding': '10px'}),
            
            html.Div([
                dcc.Graph(id='warehouse-graph', style={'height': '600px'}),
            ], style={'width': '68%', 'display': 'inline-block', 'padding': '10px'}),
        ]),
        
        # Agent Details Table
        html.Div([
            html.H3("Agent Details", style={'textAlign': 'center'}),
            html.Div(id='agent-table'),
        ], style={'padding': '20px'}),
        
        dcc.Interval(id='interval-component', interval=500, n_intervals=0),  # Update every 500ms
        dcc.Store(id='params-store', data={'n_agents': 8, 'width': 20, 'height': 15, 'task_rate': 0.1}),
    ])

