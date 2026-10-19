"""Bounded lesson progress and drawing data; never executable lesson content."""

import json
import unicodedata
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from navox.student.schemas import Input

MAX_BOARDS = 50
MAX_STROKES = 80
MAX_STROKE_POINTS = 1500
MAX_TOTAL_POINTS = 20000
MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024
MAX_REVISION = 2147483647
MAX_SAFE_INTEGER = 9007199254740991
StrictIndex = Annotated[int, Field(strict=True, ge=0, le=19)]


def valid_board_title(value: object) -> object:
    if not isinstance(value, str):
        return value
    title = value.strip()
    if not title or any(char in "<>" or unicodedata.category(char) == "Cc" for char in value):
        raise ValueError("Use a single line for the Board title")
    return title


class CatalogStep(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str
    actions: int = Field(ge=0, le=12)
    choices: int | None = Field(ge=2, le=8)


class CatalogLesson(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str
    lesson_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    steps: list[CatalogStep] = Field(min_length=1, max_length=20)
    width: int = Field(ge=1, le=12000)
    height: int = Field(ge=1, le=12000)


class Catalog(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    version: Literal[1]
    lessons: list[CatalogLesson] = Field(min_length=1, max_length=100)


@lru_cache(maxsize=1)
def authored_catalog() -> dict[str, CatalogLesson]:
    """Read the pinned catalog bundled alongside this API package."""
    catalog = Catalog.model_validate(
        json.loads(Path(__file__).with_name("catalog.json").read_text())
    )
    by_id = {lesson.id: lesson for lesson in catalog.lessons}
    if len(by_id) != len(catalog.lessons):
        raise ValueError("The authored Board catalog is invalid")
    return by_id


class BoardPoint(Input):
    x: float = Field(strict=True, ge=0, allow_inf_nan=False)
    y: float = Field(strict=True, ge=0, allow_inf_nan=False)


class BoardStroke(Input):
    id: int = Field(strict=True, ge=1, le=MAX_SAFE_INTEGER)
    points: list[BoardPoint] = Field(min_length=1, max_length=MAX_STROKE_POINTS)


class BoardSnapshot(Input):
    version: Literal[1]
    lesson_id: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9-]*$")
    lesson_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    title: str = Field(min_length=1, max_length=160)
    step_index: int = Field(strict=True, ge=0, le=19)
    revealed: int = Field(strict=True, ge=0, le=12)
    complete: bool = Field(strict=True)
    answers: dict[str, StrictIndex] = Field(max_length=20)
    marked: list[str] = Field(max_length=20)
    strokes: list[BoardStroke] = Field(max_length=MAX_STROKES)

    @field_validator("version", mode="before")
    @classmethod
    def exact_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("Choose a supported Board snapshot version")
        return value

    _title = field_validator("title", mode="before")(valid_board_title)

    @model_validator(mode="after")
    def bounded_authored_state(self) -> Self:
        lesson = authored_catalog().get(self.lesson_id)
        if lesson is None or self.lesson_version != lesson.lesson_version:
            raise ValueError("Choose a supported authored Board lesson version")
        if self.step_index >= len(lesson.steps):
            raise ValueError("The Board step is invalid")
        step = lesson.steps[self.step_index]
        if self.revealed > step.actions:
            raise ValueError("The Board reveal count is invalid")
        steps = {item.id: item for item in lesson.steps}
        for step_id, answer in self.answers.items():
            check = steps.get(step_id)
            if check is None or check.choices is None or answer >= check.choices:
                raise ValueError("The Board answer is invalid")
        if len(set(self.marked)) != len(self.marked) or any(
            step_id not in steps for step_id in self.marked
        ):
            raise ValueError("The Board revisit markers are invalid")
        if self.complete and (
            self.step_index != len(lesson.steps) - 1
            or self.revealed != step.actions
            or (step.choices is not None and step.id not in self.answers)
        ):
            raise ValueError("The Board completion state is invalid")
        if len({stroke.id for stroke in self.strokes}) != len(self.strokes):
            raise ValueError("Choose each Board stroke identifier once")
        if sum(len(stroke.points) for stroke in self.strokes) > MAX_TOTAL_POINTS:
            raise ValueError("The Board drawing exceeds the point limit")
        if any(
            point.x > lesson.width or point.y > lesson.height
            for stroke in self.strokes
            for point in stroke.points
        ):
            raise ValueError("The Board drawing is outside the lesson bounds")
        return self


class BoardCreate(Input):
    request_id: UUID
    generation: int = Field(strict=True, ge=0, le=MAX_REVISION)
    course_id: UUID | None = None
    snapshot: BoardSnapshot


class BoardUpdate(Input):
    request_id: UUID
    revision: int = Field(strict=True, ge=1, le=MAX_REVISION)
    snapshot: BoardSnapshot


class BoardErase(Input):
    request_id: UUID
    revision: int = Field(strict=True, ge=1, le=MAX_REVISION)


class BoardSummary(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    id: UUID
    revision: int = Field(strict=True, ge=1, le=MAX_REVISION)
    course_id: UUID | None
    created_at: datetime
    updated_at: datetime
    title: str = Field(min_length=1, max_length=160)
    lesson_id: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9-]*$")
    lesson_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    step_index: int = Field(strict=True, ge=0, le=19)
    complete: bool = Field(strict=True)

    _title = field_validator("title", mode="before")(valid_board_title)


class BoardView(BoardSummary):
    snapshot: BoardSnapshot


class BoardsView(BaseModel):
    generation: int
    revision: int
    boards: list[BoardSummary]
