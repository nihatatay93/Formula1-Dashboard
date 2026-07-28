from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from typing import Any

import fastf1

from app.db.session import create_session_factory
from app.ingestion.fastf1_loader import (
    FastF1SessionLoader,
    serialized_fastf1_access,
)
from app.ingestion.request_budget import FastF1RequestBudget
from app.ingestion.runtime_policy import BackfillRuntimeSettings
from app.ingestion.telemetry_measurement import (
    TelemetryMeasurementIdentity,
    measure_telemetry_frame,
    summarize_telemetry_measurements,
)


def _sample(value: str) -> TelemetryMeasurementIdentity:
    parts = value.split(":")
    if len(parts) != 5:
        raise argparse.ArgumentTypeError(
            "sample must be YEAR:ROUND:SESSION:DRIVER:LAP"
        )
    season, round_number, session, driver, lap = parts
    try:
        return TelemetryMeasurementIdentity(
            season_year=int(season),
            round_number=int(round_number),
            session_identifier=session,
            driver_identifier=driver,
            lap_number=int(lap),
        )
    except (TypeError, ValueError) as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Measure representative FastF1 lap telemetry through the "
            "persistent cache and PostgreSQL request budget."
        )
    )
    parser.add_argument(
        "--sample",
        action="append",
        required=True,
        type=_sample,
        help="YEAR:ROUND:SESSION:DRIVER:LAP; repeat for multiple samples",
    )
    return parser.parse_args()


def _load_lap_telemetry(
    loader: FastF1SessionLoader,
    identity: TelemetryMeasurementIdentity,
) -> Any:
    with serialized_fastf1_access(loader):
        session = fastf1.get_session(
            identity.season_year,
            identity.round_number,
            identity.session_identifier,
        )
        session.load(
            laps=True,
            telemetry=True,
            weather=False,
            messages=False,
        )
        driver_laps = session.laps.pick_drivers(
            identity.driver_identifier
        ).pick_laps(identity.lap_number)
        if len(driver_laps) != 1:
            raise RuntimeError(
                "sample identity must resolve to exactly one FastF1 lap"
            )
        lap = driver_laps.iloc[0]
        try:
            return lap.get_telemetry(frequency="original")
        except KeyError as error:
            car_data = lap.get_car_data()
            if car_data.empty:
                raise RuntimeError(
                    "sample lap has no usable FastF1 car telemetry"
                ) from error
            return car_data.add_distance().add_relative_distance()


def main() -> None:
    args = parse_args()
    cache_path = os.environ["FASTF1_CACHE_PATH"]
    settings = BackfillRuntimeSettings.from_environment()
    session_factory = create_session_factory()
    loader = FastF1SessionLoader(
        cache_path,
        request_budget=FastF1RequestBudget(
            session_factory=session_factory,
            operation="archive",
            settings=settings,
        ),
    )

    measurements = []
    for identity in args.sample:
        frame = _load_lap_telemetry(loader, identity)
        measurements.append(
            measure_telemetry_frame(frame, identity=identity)
        )

    payload = {
        "fastf1_version": fastf1.__version__,
        "measurements": [
            measurement.as_json_dict() for measurement in measurements
        ],
        "summary": asdict(summarize_telemetry_measurements(measurements)),
    }
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
