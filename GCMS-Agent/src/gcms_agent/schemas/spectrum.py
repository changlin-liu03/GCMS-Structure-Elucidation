from typing import Literal, TypedDict


class Conditions(TypedDict, total=False):
    ionization: Literal["EI"]
    energy_ev: float
    energy_is_assumed: bool
    resolution: Literal["nominal"]
    intensity_encoding: Literal["raw_linear"]


class Peak(TypedDict):
    mz: int
    raw_intensity: float
    intensity_encoding: Literal["raw_linear"]
    lower: float
    upper: float
    intensity: float


class InferenceInput(TypedDict):
    sample_id: str
    input_ids: list[int]
    intensity: list[float]
    generated: list[str]
    conditions: Conditions

