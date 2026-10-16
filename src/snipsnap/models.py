"""Typed data models shared by the analysers, the JSON report and the viewer."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

DeadKind = Literal["function", "method", "class", "import"]
Analysis = Literal["dead_code", "duplicates", "test_only"]


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class DeadItem(_Model):
    kind: DeadKind
    name: str
    qualname: str
    path: str
    line: int = Field(ge=1, description="1-based, inclusive")
    end_line: int = Field(ge=1, description="1-based, inclusive")


class OnlyTestedItem(DeadItem):
    tested_by: tuple[str, ...] = Field(
        description=(
            "test files that reference the name; empty if only reached via other test-only code"
        )
    )


class FunctionRef(_Model):
    qualname: str
    path: str
    line: int = Field(ge=1, description="1-based, inclusive, includes decorators")
    end_line: int = Field(ge=1, description="1-based, inclusive")


class DuplicateGroup(_Model):
    kind: Literal["exact", "similar"]
    similarity: float = Field(gt=0, le=1)
    functions: tuple[FunctionRef, ...]


class DeadCodeFinding(DeadItem):
    id: str
    suggested_action: str
    source: str | None = None


class OnlyTestedFinding(OnlyTestedItem):
    id: str
    suggested_action: str
    source: str | None = None


class DuplicateFunction(FunctionRef):
    source: str | None = None


class DuplicateFinding(_Model):
    id: str
    kind: Literal["exact", "similar"]
    similarity: float = Field(gt=0, le=1)
    functions: list[DuplicateFunction]
    suggested_action: str


class Summary(_Model):
    files_scanned: int
    analyses: list[Analysis]
    dead_code: int
    duplicate_groups: int
    test_only: int = 0


class Report(_Model):
    version: Literal[1] = 1
    root: str = Field(description="all finding paths are relative to this directory")
    summary: Summary
    dead_code: list[DeadCodeFinding]
    duplicates: list[DuplicateFinding]
    test_only: list[OnlyTestedFinding] = Field(default_factory=list)

    def to_json(self) -> str:
        return self.model_dump_json(indent=2, exclude_none=True)
