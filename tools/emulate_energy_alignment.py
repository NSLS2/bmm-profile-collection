"""Run the BMM energy-alignment workflow against local simulated devices.

From an IPython session in the profile collection repository::

    %run tools/emulate_energy_alignment.py

The script uses its own RunEngine, ``SynAxis``/``SynSignal`` devices, and an
in-process Tiled ``CatalogOfBlueskyRuns``. It never imports or moves live
BMM devices. The real energy-alignment plan, Blop agent, image evaluator,
dashboard callback, and debug viewer are used unchanged.

After the run, these names remain available in IPython:

``energy_alignment_emulator``
    Simulator, profile, resources, catalog, and captured run UIDs.
``energy_alignment_results``
    The energy map returned by ``search_for_optimal_positions``.
``energy_alignment_debug_figure``
    The final energy's Matplotlib debug figure, unless ``--no-debug`` was used.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass, replace
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any, Sequence

from bluesky_tiled_plugins import CatalogOfBlueskyRuns, TiledWriter
from bluesky import RunEngine
from bluesky.plan_stubs import null, sleep
import numpy as np
from ophyd.sim import SynAxis, SynSignal
from tiled.catalog import in_memory
from tiled.client import Context, from_context
from tiled.server.app import build_app

try:
    from BMM.optimization import (
        XAS_SI111_ALIGNMENT,
        AlignmentCostConfig,
        BeamEvaluationConfig,
        EnergyAlignmentResources,
        OptimizationConfig,
        search_for_optimal_positions,
        show_energy_alignment_debug,
    )
except ModuleNotFoundError:
    # Allow direct execution from a checkout without requiring PYTHONPATH.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "startup"))
    from BMM.optimization import (
        XAS_SI111_ALIGNMENT,
        AlignmentCostConfig,
        BeamEvaluationConfig,
        EnergyAlignmentResources,
        OptimizationConfig,
        search_for_optimal_positions,
        show_energy_alignment_debug,
    )


_DOF_NAMES = ("dcm_roll", "m2_yaw", "m2_lateral")
_ENERGY_MODELS = {
    "Fe": (7112.0, {"dcm_roll": 0.35, "m2_yaw": -0.25, "m2_lateral": 0.20}),
    "Cu": (8979.0, {"dcm_roll": -0.30, "m2_yaw": 0.20, "m2_lateral": -0.25}),
}


@dataclass
class _BeamlineState:
    element: str | None = None
    energy: float = 7000.0


class _RunTracker:
    """Remember target, acquisition, and outer optimization UIDs by energy."""

    def __init__(self, state: _BeamlineState):
        self.state = state
        self.reset()

    def reset(self) -> None:
        self.target_uid: str | None = None
        self.acquisition_uids: dict[str, list[str]] = defaultdict(list)
        self.optimization_uids: dict[str, list[str]] = defaultdict(list)

    def __call__(self, name: str, document: dict[str, Any]) -> None:
        if name != "start":
            return
        uid = document["uid"]
        if document.get("plan_name") == "acquire_target_position":
            self.target_uid = uid
        if self.state.element is None:
            return
        if "blop_suggestions" in document:
            self.acquisition_uids[self.state.element].append(uid)
        if document.get("plan_name") == "optimize":
            self.optimization_uids[self.state.element].append(uid)


class EnergyAlignmentEmulator:
    """Own a fully local energy-alignment simulation suitable for IPython use."""

    dashboard_url = "http://127.0.0.1:8050"

    def __init__(
        self,
        *,
        iterations: int = 8,
        exposure_time: float = 0.35,
        edge_delay: float = 1.0,
    ) -> None:
        self.edge_delay = edge_delay
        self.state = _BeamlineState()
        self.actuators = {
            name: SynAxis(name=name, value=0.0) for name in _DOF_NAMES
        }
        self.camera = SynSignal(
            name="image",
            func=self._camera_image,
            exposure_time=exposure_time,
        )
        self.ion_chamber = SynSignal(
            name="i0",
            func=self._ion_chamber_intensity,
            exposure_time=exposure_time,
        )

        self._tiled_storage = TemporaryDirectory(
            prefix="bmm-energy-alignment-emulator-"
        )
        storage_root = Path(self._tiled_storage.name)
        array_storage = storage_root / "arrays"
        array_storage.mkdir()
        tiled_tree = in_memory(
            writable_storage=[
                array_storage,
                f"sqlite:///{storage_root / 'tables.sqlite'}",
            ],
            specs=[{"name": "CatalogOfBlueskyRuns", "version": "3.0"}],
        )
        self.tiled_writing_client = from_context(
            Context.from_app(build_app(tiled_tree))
        )
        if not isinstance(self.tiled_writing_client, CatalogOfBlueskyRuns):
            raise RuntimeError("Tiled did not create a Bluesky run catalog client")
        self.catalog = self.tiled_writing_client.v2
        self.tiled_writer = TiledWriter(self.tiled_writing_client, batch_size=1)
        self.run_engine = RunEngine({}, call_returns_result=True)
        self.run_engine.subscribe(self.tiled_writer)
        self.tracker = _RunTracker(self.state)
        self.run_engine.subscribe(self.tracker)

        self.profile = replace(
            XAS_SI111_ALIGNMENT,
            camera="camera",
            dof_bounds={name: (-1.0, 1.0) for name in _DOF_NAMES},
            search_half_widths={name: 0.9 for name in _DOF_NAMES},
            evaluation=BeamEvaluationConfig(
                image_field="image",
                intensity_field="i0",
                x_crop=(4, 77),
                y_crop=(4, 47),
                blur_sigma=1.0,
                upscale_factor=2,
            ),
            cost=AlignmentCostConfig(
                position_tolerance_px=3.0,
                focus_weight=0.5,
                dof_weight=0.08,
            ),
            minimum_intensity_fraction=0.5,
            optimization=OptimizationConfig(
                iterations=iterations,
                initialization_budget=min(3, iterations),
                initialize_with_center=True,
            ),
            change_edge_kwargs={
                "focus": False,
                "no_hslits": False,
                "mirror": True,
                "xrd": False,
                "bender": False,
            },
        )
        self.resources = EnergyAlignmentResources(
            catalog=self.catalog,
            actuators=self.actuators,
            sensors={"camera": self.camera, "i0": self.ion_chamber},
            change_edge_plan=self._change_edge,
            prompt_state=SimpleNamespace(prompt=True),
            read_energy=lambda: self.state.energy,
        )
        self.energies: tuple[str, ...] = ()
        self.results: dict[str, Any] | None = None
        self.debug_figure = None

    def _target_positions(self) -> dict[str, float]:
        if self.state.element is None:
            return {name: 0.0 for name in _DOF_NAMES}
        return _ENERGY_MODELS[self.state.element][1]

    def _position_errors(self) -> dict[str, float]:
        target = self._target_positions()
        return {
            name: float(self.actuators[name].position) - target[name]
            for name in _DOF_NAMES
        }

    def _camera_image(self) -> np.ndarray:
        errors = self._position_errors()
        center_x = 40.0 + 13.0 * errors["dcm_roll"] + 4.0 * errors["m2_lateral"]
        center_y = 25.0 + 8.0 * errors["m2_yaw"]
        sigma_x = 3.5 * (
            1.0
            + 0.35 * errors["m2_yaw"] ** 2
            + 0.15 * errors["m2_lateral"] ** 2
        )
        sigma_y = 2.4 * (1.0 + 0.15 * errors["dcm_roll"] ** 2)
        radius_squared = sum(error**2 for error in errors.values())
        amplitude = 1000.0 * np.exp(-0.15 * radius_squared)
        y, x = np.indices((51, 81))
        return 2.0 + amplitude * np.exp(
            -0.5
            * (
                ((x - center_x) / sigma_x) ** 2
                + ((y - center_y) / sigma_y) ** 2
            )
        )

    def _ion_chamber_intensity(self) -> float:
        radius_squared = sum(error**2 for error in self._position_errors().values())
        return float(250_000.0 + 750_000.0 * np.exp(-1.2 * radius_squared))

    def _change_edge(self, element: str, **kwargs: Any):
        try:
            energy, _target = _ENERGY_MODELS[element]
        except KeyError as exc:
            choices = ", ".join(_ENERGY_MODELS)
            raise ValueError(
                f"The emulator supports {choices}; received {element!r}"
            ) from exc
        self.state.element = element
        self.state.energy = energy
        print(f"[simulator] change_edge({element!r}, {kwargs}) -> {energy:.1f} eV")
        if self.edge_delay:
            yield from sleep(self.edge_delay)
        else:
            yield from null()

    def _reset(self) -> None:
        self.state.element = None
        self.state.energy = 7000.0
        self.resources.prompt_state.prompt = True
        self.tracker.reset()
        for actuator in self.actuators.values():
            actuator.set(0.0).wait()

    def run(self, energies: Sequence[str] = ("Fe", "Cu")) -> dict[str, Any]:
        """Run the production search plan against this simulator."""
        self._reset()
        self.energies = tuple(energies)
        print("Simulator only: no EPICS devices or live beamline objects are loaded.")
        print(f"Open the live dashboard at {self.dashboard_url}")
        print(
            "The server starts after target acquisition and remains available in "
            "this IPython process."
        )
        result = self.run_engine(
            search_for_optimal_positions(
                list(self.energies),
                profile=self.profile,
                resources=self.resources,
            )
        )
        self.results = result.plan_result
        print(f"[simulator] target UID: {self.tracker.target_uid}")
        print(
            "[simulator] outer optimization UIDs: "
            f"{dict(self.tracker.optimization_uids)}"
        )
        return self.results

    def show_debug(self, energies: Sequence[str] | None = None):
        """Render the production Matplotlib diagnostics for completed emulated runs."""
        requested = self.energies if energies is None else tuple(energies)
        if not requested:
            raise RuntimeError("Run the emulator before requesting debug plots")
        missing = [energy for energy in requested if not self.tracker.optimization_uids[energy]]
        if missing:
            raise RuntimeError(f"No completed optimization runs for {missing!r}")
        uids = [self.tracker.optimization_uids[energy][-1] for energy in requested]
        self.debug_figure = show_energy_alignment_debug(
            uids[0] if len(uids) == 1 else uids,
            profile=self.profile,
            resources=self.resources,
        )
        from matplotlib import pyplot as plt

        plt.show(block=False)
        return self.debug_figure


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--energies", nargs="+", default=["Fe", "Cu"])
    parser.add_argument("--iterations", type=int, default=8)
    parser.add_argument("--exposure-time", type=float, default=0.35)
    parser.add_argument("--edge-delay", type=float, default=1.0)
    parser.add_argument(
        "--no-debug",
        action="store_true",
        help="Do not open the Matplotlib debug figure after optimization.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    _args = _parse_args()
    energy_alignment_emulator = EnergyAlignmentEmulator(
        iterations=_args.iterations,
        exposure_time=_args.exposure_time,
        edge_delay=_args.edge_delay,
    )
    energy_alignment_results = energy_alignment_emulator.run(_args.energies)
    energy_alignment_debug_figure = (
        None
        if _args.no_debug
        else energy_alignment_emulator.show_debug([_args.energies[-1]])
    )
    print("[simulator] Objects retained in IPython as:")
    print("  energy_alignment_emulator")
    print("  energy_alignment_results")
    print("  energy_alignment_debug_figure")
    print("[simulator] Reopen one energy: energy_alignment_emulator.show_debug(['Fe'])")
    print("[simulator] Compare all energies: energy_alignment_emulator.show_debug()")
