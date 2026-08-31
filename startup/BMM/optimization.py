import numpy as np
from blop import RangeDOF, Objective, Agent, OutcomeConstraint
from blop.protocols import EvaluationFunction
from bluesky.preprocessors import inject_md_wrapper
from ax.api.protocols import IMetric
from tiled.client.container import Container
import pickle


from BMM.edge import change_edge
from BMM.user_ns.dcm import dcm
from BMM.user_ns.instruments import m2
from BMM.user_ns.detectors import ic0, cam8
from BMM.functions import not_at_edge # we may need this again
from BMM.user_ns.bmm import BMMuser

tiled_client = bmm_catalog

# ------ DOFs ------------
dcm_roll_dof = RangeDOF(
    actuator=dcm.roll,
    bounds=(-0.365 - 10, -0.365 + 10),
    parameter_type="float",
)
m2_yaw_dof = RangeDOF(
    actuator=m2.yaw,
    bounds=(-2, 2),
    parameter_type="float",
)
m2_lateral_dof = RangeDOF(
    actuator=m2.lateral,
    bounds=(-2, 2),
    parameter_type="float",
)
dofs = [dcm_roll_dof, m2_yaw_dof, m2_lateral_dof]

# -------- Objectives ---------
lateral_distance_obj = Objective(name="lateral_distance", minimize=True)

# we can make intensity an objective if we are getting insufficient optimal beam
intensity_obj = Objective(name="intensity", minimize=False)
intensity_metric = IMetric(name="intensity")

objectives = [
    lateral_distance_obj
]

# -------- Outcome constraints ---------
distance_constraint = OutcomeConstraint("x <= 2", x=lateral_distance_obj) # TODO verify this again
intensity_constraint = OutcomeConstraint("x >= 1000000", x=intensity_metric) # TODO verify this again

outcome_constraints = [
    intensity_constraint
]

def _device_name(device) -> str:
    return getattr(device, "name", str(device))


def _optimization_metadata(energy: str, reference_scan_uid: str = None) -> dict:
    try:
        beamline_energy = float(dcm.energy.readback.get())
    except Exception:
        beamline_energy = float(dcm.energy.position)

    return {
        "Beamline": {
            "energy": beamline_energy,
        },
        "BMM_agent": {
            "agent": "blop",
            "plan_name": "search_for_optimal_positions",
            "requested_energy": energy,
            "reference_scan_uid": reference_scan_uid,
            "dofs": [
                {
                    "name": _device_name(dof),
                    "actuator": _device_name(dof.actuator),
                    "bounds": list(dof.bounds),
                    "parameter_type": getattr(dof, "parameter_type", None),
                }
                for dof in dofs
            ],
            "sensors": [_device_name(sensor) for sensor in (cam8, ic0)],
            "objectives": [objective.name for objective in objectives],
            "outcome_constraints": [str(constraint) for constraint in outcome_constraints],
        },
    }


def optimization_metadata_wrapper(plan, energy: str, reference_scan_uid: str = None):
    md = _optimization_metadata(energy, reference_scan_uid=reference_scan_uid)
    return inject_md_wrapper(plan, md)

# use this function in bsui for sanity checking
def compute_stats(uid: str):
    image = tiled_client[uid]['primary']['data']['cam8-image'].read()
    intensity_ic0 = tiled_client[uid]['primary']['I0'].read()

    gray = image.squeeze().astype(np.float64)
    if gray.ndim == 3:
        gray = gray.mean(axis=-1)

    crop_region_x = [900, 1040]

    # do cropping here (900, 1040) TODO confirm this again
    cropped_y_profile = gray.sum(axis=0)[crop_region_x[0]:crop_region_x[1]]

    # get the maximum column position (lateral position)
    lateral_position = np.argmax(cropped_y_profile) + crop_region_x[0]

    # get intensity of cropped area
    cropped_intensity = cropped_y_profile.sum()

    print(f"lateral_position: {lateral_position}, cropped_intensity={cropped_intensity}, ic0_intensity={intensity_ic0}")

# --------- Custom evaluation ---------
class ImageEvaluation(EvaluationFunction):
    def __init__(self, tiled_client: Container, reference_scan_uid: str):
        self.tiled_client = tiled_client

        # take an image before starting optimizations, use this as our "baseline" position/intensity
        ref_image = self.tiled_client[reference_scan_uid]['primary']['data']['cam-8_image'].read()
        ref_lateral_position, ref_intensity = self._compute_stats(ref_image)
        self.target_lateral_position = ref_lateral_position
        self.target_intensity = ref_intensity

    def _compute_stats(self, image: np.ndarray) -> tuple[float, float]:
        gray = image.squeeze().astype(np.float64)
        if gray.ndim == 3:
            gray = gray.mean(axis=-1)
        
        crop_region_x = [900, 1040]

        # do cropping here (900, 1040) TODO confirm this again
        cropped_y_profile = gray.sum(axis=0)[crop_region_x[0]:crop_region_x[1]]

        # get the maximum column position (lateral position)
        lateral_position = np.argmax(cropped_y_profile) + crop_region_x[0]

        # get intensity of cropped area
        cropped_intensity = cropped_y_profile.sum()
        
        return lateral_position, cropped_intensity

    def __call__(self, uid: str, suggestions) -> list[dict]:
        outcomes = []
        run = self.tiled_client[uid]
        
        image = run['primary']['data']['cam-8_image'].read()
        intensity_ic0 = run['primary']['I0'].read()

        suggestion_ids = [suggestion["_id"] for suggestion in run.metadata["start"]["blop_suggestions"]]

        for idx, sid in enumerate(suggestion_ids):
            lateral_position, intensity = self._compute_stats(image, intensity_ic0)
            lateral_distance = abs(self.target_lateral_position - lateral_position)

            outcome = {
                "_id": sid,
                "lateral_distance": lateral_distance,
                "intensity": intensity,
            }

            outcomes.append(outcome)

        return outcomes

def search_for_optimal_positions(energies: list[str], reference_scan_uid: str, energy_map_filename: str = None):
    """A plan that uses Blop to search for optimal motor setpoints.

    Returns
    -------
    dict
        A lookup table mapping energy values -> motor positions.
    """
    energy_map = {}
    BMMuser.prompt = False

    for energy in energies:
        # change energy
        yield from change_edge(energy, focus=True, no_hslits=True, mirror=False)

        # initialize agent
        agent = Agent(
                    sensors=[cam8, ic0],
                    dofs=dofs,
                    objectives=objectives,
                    evaluation_function=ImageEvaluation(tiled_client, reference_scan_uid=reference_scan_uid),
                    outcome_constraints=outcome_constraints
                )

        # TODO is this the generation strategy we want?
        agent.ax_client.configure_generation_strategy(initialization_budget=1, initialize_with_center=False)

        max_iter = 20
        yield from optimization_metadata_wrapper(agent.optimize(max_iter), energy, reference_scan_uid, max_iter)

        best_points = agent.get_best_points()

        print(f"best point for {energy} is {best_points}")
        energy_map[energy] = best_points

        if energy_map_filename:
            with open(energy_map_filename, "wb") as f:
                pickle.dump(energy_map, f)

    print(f"{energy_map=}")
    BMMuser.prompt = True

# --------- Dash Live Plotting Callback ---------

"""Live 2D surrogate-model visualization for the Ax :class:`~blop.ax.agent.Agent`.

This module provides :class:`SurrogateModelDashCallback`, a Bluesky callback that
renders the Ax surrogate model's predicted mean as a 2D heatmap and serves it in a
`Plotly Dash <https://dash.plotly.com/>`_ app that updates live as an optimization
runs.

The Dash app exposes three dropdowns:

- **X axis**: any degree of freedom (DOF).
- **Y axis**: any (other) degree of freedom.
- **Objective**: any one of the configured objectives.

The heatmap shows the surrogate model's posterior mean for the selected objective as
a function of the two selected DOFs, with all other DOFs held at the centre of their
range (or their first choice value). Observed trials are overlaid as points.

Notes
-----
Plotly Dash is an optional runtime dependency. It is imported lazily so that simply
importing this module (or the :mod:`blop.callbacks` package) does not require Dash to
be installed. Install it with ``pip install dash``.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

import numpy as np
from bluesky.callbacks import CallbackBase
from event_model import Event, RunStart, RunStop

if TYPE_CHECKING:
    import plotly.graph_objects as go

    from blop.ax.agent import _AxAgentMixin


def _to_list(value: Any) -> list:
    """Coerce a scalar, numpy array, or iterable into a plain list."""
    if hasattr(value, "tolist"):
        result = value.tolist()
        return result if isinstance(result, list) else [result]
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _py(value: Any) -> Any:
    """Convert a numpy scalar to its native Python equivalent."""
    return value.item() if hasattr(value, "item") else value


class SurrogateModelDashCallback(CallbackBase):
    """A Bluesky callback that serves a live 2D surrogate-model heatmap via Plotly Dash.

    Subscribe an instance to an :class:`~blop.ax.agent.Agent` (via
    :meth:`Agent.subscribe <blop.ax.agent.Agent.subscribe>`) and call :meth:`serve`
    to start the Dash server. As the agent ingests new trials during an optimization
    run, the callback bumps an internal version counter; the Dash app polls this
    counter on a timer and re-renders the surrogate heatmap whenever new data arrives.

    Parameters
    ----------
    agent : Agent
        The Ax agent whose surrogate model will be visualized. The callback reads the
        agent's DOFs, objectives, and underlying Ax ``Client`` for predictions.
    resolution : int, optional
        The number of grid points per axis used to sample the surrogate model for
        continuous DOFs. Default is 41.
    update_interval_ms : int, optional
        How often (in milliseconds) the Dash app checks for new data. Default is 2000.

    See Also
    --------
    blop.ax.agent.Agent.plot_objective : One-off contour plot via Ax analyses.

    Examples
    --------
    >>> from blop.callbacks.surrogate_dash import SurrogateModelDashCallback
    >>> viz = SurrogateModelDashCallback(agent)
    >>> agent.subscribe(viz)
    >>> # In a separate thread or process, start the server:
    >>> viz.serve(port=8050)  # doctest: +SKIP
    """

    def __init__(
        self,
        agent: _AxAgentMixin,
        resolution: int = 41,
        update_interval_ms: int = 100,
    ) -> None:
        super().__init__()
        # Imported here (not at module scope) so that ax is only required when the
        # callback is actually constructed against an agent.
        from ax.core.parameter import ChoiceParameter, ParameterType, RangeParameter

        self._agent = agent
        self._resolution = resolution
        self._update_interval_ms = update_interval_ms

        self._lock = threading.Lock()
        self._version = 0
        self._observed: dict[str, list[Any]] = {}

        # Extract DOF metadata from the underlying Ax experiment.
        parameters = agent.ax_client._experiment.parameters
        self._param_info: dict[str, dict[str, Any]] = {}
        for name, parameter in parameters.items():
            if isinstance(parameter, RangeParameter):
                self._param_info[name] = {
                    "kind": "range",
                    "lower": float(parameter.lower),
                    "upper": float(parameter.upper),
                    "is_int": parameter.parameter_type == ParameterType.INT,
                }
            elif isinstance(parameter, ChoiceParameter):
                self._param_info[name] = {
                    "kind": "choice",
                    "values": [_py(v) for v in parameter.values],
                }
        self._dof_names = list(self._param_info.keys())

        # Extract objective (metric) names.
        opt_config = agent.ax_client._experiment.optimization_config
        if opt_config is None:
            raise ValueError("The agent's optimization has not been configured; no objectives to plot.")
        self._objective_names = list(opt_config.objective.metric_names)

    # -- Bluesky callback hooks ------------------------------------------------

    def start(self, doc: RunStart) -> None:
        """Bump the version so the app refreshes at the start of a run."""
        with self._lock:
            self._version += 1

    def event(self, doc: Event) -> Event:
        """Record observed DOF values and bump the version on each new trial."""
        data = doc.get("data", {})
        with self._lock:
            for name in self._dof_names:
                if name in data:
                    self._observed.setdefault(name, []).extend(_to_list(data[name]))
            self._version += 1
        return doc

    def stop(self, doc: RunStop) -> RunStop | None:
        """Bump the version at the end of a run."""
        with self._lock:
            self._version += 1
        return doc

    # -- Public accessors ------------------------------------------------------

    @property
    def dof_names(self) -> list[str]:
        """The names of the degrees of freedom available for the x/y axes."""
        return list(self._dof_names)

    @property
    def objective_names(self) -> list[str]:
        """The names of the objectives available to visualize."""
        return list(self._objective_names)

    @property
    def version(self) -> int:
        """A monotonically increasing counter that changes when new data arrives."""
        with self._lock:
            return self._version

    # -- Grid / figure construction --------------------------------------------

    def _axis_values(self, name: str, resolution: int) -> tuple[np.ndarray, bool]:
        """Return the sample values for an axis and whether it is discrete."""
        info = self._param_info[name]
        if info["kind"] == "range":
            values = np.linspace(info["lower"], info["upper"], resolution)
            if info["is_int"]:
                values = np.unique(np.round(values).astype(int))
            return values, bool(info["is_int"])
        return np.array(info["values"], dtype=object), True

    def _fixed_value(self, name: str) -> Any:
        """Return the held-fixed value for a DOF not on the x/y axes."""
        info = self._param_info[name]
        if info["kind"] == "range":
            mid = (info["lower"] + info["upper"]) / 2.0
            return int(round(mid)) if info["is_int"] else mid
        return info["values"][0]

    def _coerce(self, name: str, value: Any) -> Any:
        """Coerce a grid value to the DOF's expected Python type."""
        info = self._param_info[name]
        if info["kind"] == "range" and info["is_int"]:
            return int(round(float(value)))
        return _py(value)

    def compute_figure(
        self,
        x_name: str,
        y_name: str,
        objective_name: str,
        resolution: int | None = None,
    ) -> go.Figure:
        """Build a Plotly heatmap of the surrogate model's predicted mean.

        Parameters
        ----------
        x_name : str
            The DOF to place on the x axis.
        y_name : str
            The DOF to place on the y axis.
        objective_name : str
            The objective (metric) whose predicted mean is shown.
        resolution : int | None, optional
            Grid resolution per axis. Defaults to the value passed at construction.

        Returns
        -------
        plotly.graph_objects.Figure
            The heatmap figure. If the surrogate model cannot yet make predictions
            (e.g. too few trials), a figure with an explanatory annotation is returned.
        """
        import plotly.graph_objects as go

        resolution = resolution or self._resolution

        if x_name == y_name:
            return self._message_figure("Select two different DOFs for the x and y axes.")

        x_values, _ = self._axis_values(x_name, resolution)
        y_values, _ = self._axis_values(y_name, resolution)

        fixed = {name: self._fixed_value(name) for name in self._dof_names}
        points: list[dict[str, Any]] = []
        for y_val in y_values:
            for x_val in x_values:
                point = dict(fixed)
                point[x_name] = self._coerce(x_name, x_val)
                point[y_name] = self._coerce(y_name, y_val)
                points.append(point)

        try:
            predictions = self._agent.ax_client.predict(points)
        except Exception as exc:  # noqa: BLE001 - surface any model-not-ready error to the UI
            return self._message_figure(
                f"Surrogate model is not ready to predict yet.<br>Run more trials to continue.<br><br>({exc})"
            )

        try:
            z = np.array([pred[objective_name][0] for pred in predictions], dtype=float)
        except KeyError:
            return self._message_figure(f"Objective '{objective_name}' is not available in the model predictions.")

        z = z.reshape(len(y_values), len(x_values))

        figure = go.Figure(
            data=go.Heatmap(
                x=list(x_values),
                y=list(y_values),
                z=z,
                colorscale="Viridis",
                colorbar={"title": objective_name},
                hovertemplate=f"{x_name}: %{{x}}<br>{y_name}: %{{y}}<br>{objective_name}: %{{z:.4g}}<extra></extra>",
            )
        )

        # Overlay observed trials, if any.
        with self._lock:
            observed_x = list(self._observed.get(x_name, []))
            observed_y = list(self._observed.get(y_name, []))
        n_observed = min(len(observed_x), len(observed_y))
        if n_observed:
            figure.add_trace(
                go.Scatter(
                    x=observed_x[:n_observed],
                    y=observed_y[:n_observed],
                    mode="markers",
                    marker={"color": "white", "size": 7, "line": {"color": "black", "width": 1}},
                    name="observed",
                    hovertemplate=f"{x_name}: %{{x}}<br>{y_name}: %{{y}}<extra>observed</extra>",
                )
            )

        figure.update_layout(
            title=f"Surrogate mean of '{objective_name}'",
            xaxis_title=x_name,
            yaxis_title=y_name,
            margin={"l": 60, "r": 30, "t": 50, "b": 50},
            template="plotly_white",
        )
        return figure

    @staticmethod
    def _message_figure(message: str) -> go.Figure:
        """Return an empty figure displaying a centered message."""
        import plotly.graph_objects as go

        figure = go.Figure()
        figure.add_annotation(text=message, showarrow=False, font={"size": 14}, xref="paper", yref="paper", x=0.5, y=0.5)
        figure.update_layout(
            xaxis={"visible": False},
            yaxis={"visible": False},
            margin={"l": 20, "r": 20, "t": 20, "b": 20},
            template="plotly_white",
        )
        return figure

    # -- Dash app --------------------------------------------------------------

    def build_app(self, **dash_kwargs: Any):
        """Build and return a Dash app that renders the live surrogate heatmap.

        Parameters
        ----------
        **dash_kwargs : Any
            Additional keyword arguments forwarded to :class:`dash.Dash`.

        Returns
        -------
        dash.Dash
            The configured Dash application. Call ``app.run(...)`` to start it, or use
            :meth:`serve` for convenience.
        """
        from dash import Dash, Input, Output, dcc, html

        default_x = self._dof_names[0]
        default_y = self._dof_names[1] if len(self._dof_names) > 1 else self._dof_names[0]
        default_objective = self._objective_names[0]

        app = Dash(__name__, **dash_kwargs)
        app.layout = html.Div(
            [
                html.H2("Surrogate model", style={"textAlign": "center"}),
                html.Div(
                    [
                        html.Div(
                            [
                                html.Label("X axis"),
                                dcc.Dropdown(
                                    options=[{"label": n, "value": n} for n in self._dof_names],
                                    value=default_x,
                                    id="surrogate-x-dropdown",
                                    clearable=False,
                                ),
                            ],
                            style={"flex": "1", "padding": "0 8px"},
                        ),
                        html.Div(
                            [
                                html.Label("Y axis"),
                                dcc.Dropdown(
                                    options=[{"label": n, "value": n} for n in self._dof_names],
                                    value=default_y,
                                    id="surrogate-y-dropdown",
                                    clearable=False,
                                ),
                            ],
                            style={"flex": "1", "padding": "0 8px"},
                        ),
                        html.Div(
                            [
                                html.Label("Objective"),
                                dcc.Dropdown(
                                    options=[{"label": n, "value": n} for n in self._objective_names],
                                    value=default_objective,
                                    id="surrogate-objective-dropdown",
                                    clearable=False,
                                ),
                            ],
                            style={"flex": "1", "padding": "0 8px"},
                        ),
                    ],
                    style={"display": "flex", "maxWidth": "900px", "margin": "0 auto"},
                ),
                dcc.Graph(id="surrogate-graph", style={"height": "70vh"}),
                dcc.Interval(id="surrogate-interval", interval=self._update_interval_ms, n_intervals=0),
                dcc.Store(id="surrogate-rendered-version", data=-1),
            ]
        )

        @app.callback(
            Output("surrogate-graph", "figure"),
            Output("surrogate-rendered-version", "data"),
            Input("surrogate-x-dropdown", "value"),
            Input("surrogate-y-dropdown", "value"),
            Input("surrogate-objective-dropdown", "value"),
            Input("surrogate-interval", "n_intervals"),
            Input("surrogate-rendered-version", "data"),
        )
        def _update_graph(x_name, y_name, objective_name, _n_intervals, rendered_version):
            from dash import ctx, no_update

            current_version = self.version
            # On a timer tick, only recompute when new data has arrived. Dropdown
            # changes always force a recompute.
            triggered_by_timer = ctx.triggered_id == "surrogate-interval"
            if triggered_by_timer and current_version == rendered_version:
                return no_update, no_update

            figure = self.compute_figure(x_name, y_name, objective_name)
            return figure, current_version

        return app

    def serve(self, host: str = "127.0.0.1", port: int = 8050, debug: bool = False, **run_kwargs: Any) -> None:
        """Build the Dash app and start the development server (blocking).

        Parameters
        ----------
        host : str, optional
            The host interface to bind to. Default is ``"127.0.0.1"``.
        port : int, optional
            The port to serve on. Default is 8050.
        debug : bool, optional
            Whether to run Dash in debug mode. Default is False.
        **run_kwargs : Any
            Additional keyword arguments forwarded to ``dash.Dash.run``.

        Notes
        -----
        This call blocks. To run alongside a Bluesky ``RunEngine`` in the same process,
        start it in a background thread, e.g.::

            import threading
            threading.Thread(target=viz.serve, kwargs={"port": 8050}, daemon=True).start()
        """
        app = self.build_app()
        app.run(host=host, port=port, debug=debug, **run_kwargs)
