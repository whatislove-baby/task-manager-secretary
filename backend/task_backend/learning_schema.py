"""Explicit model-facing learning inputs; server snapshots are never inputs.

The same bounded operations remain checked against the example by LearningService.
These schemas explain the wire format before a model calls the tool, rather than
asking it to reconstruct writable inputs from an enriched read response.
"""
from __future__ import annotations

from typing import Annotated, Literal, Union
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


Name = Annotated[str, Field(min_length=1, max_length=200)]
Names = Annotated[list[Name], Field(max_length=100)]
NonemptyNames = Annotated[list[Name], Field(min_length=1, max_length=100)]
Cell = Union[str, int, None]


class LearningInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, validate_default=True)


class LearningScope(LearningInput):
    kind: Literal['customer', 'course', 'personal']
    label: Name = Field(description='Readable label only; never an identity key.')
    emails: Annotated[list[str], Field(max_length=20)] = Field(description='Exact bare sender addresses, not display names. Customer/course requires at least one.')
    contextKey: Annotated[str, Field(max_length=200)] = Field(default='', description='Course code required for course scope. Preserve the prior scope for revisions.')


class LearningSource(LearningInput):
    kind: Literal['file', 'mail']
    id: str = Field(min_length=1, description='Existing managed file/mail ID. Only kind and id are writable; omit name, sha256, role, mailSources, contentHash and other read-response metadata.')


class LearningConcat(LearningInput):
    target: Name
    columns: NonemptyNames = Field(description='Input column names, in joining order.')
    separator: Annotated[str, Field(max_length=20)]
    padLast: Annotated[int, Field(ge=1, le=20)] | None = Field(default=None, description='Optional zero-padding width for the final numeric revision component; never invent a missing revision.')


class LearningCommercial(LearningInput):
    currency: Annotated[str, Field(pattern=r'^[A-Z]{3}$')] = Field(description='Explicitly established three-letter currency, never inferred from another customer.')
    taxBasis: Literal['inclusive', 'exclusive', 'not_applicable']
    unit: Annotated[str, Field(min_length=1, max_length=50)] = Field(description='Explicit quantity/price unit. Ask if unknown; do not guess PCS.')


class ColumnOperand(LearningInput):
    column: Name


class ValueOperand(LearningInput):
    value: str | int = Field(description='Exact decimal string or integer. No floats, formulas or expressions.')


class DropDuplicatesOperation(LearningInput):
    op: Literal['drop_duplicates']
    subset: NonemptyNames | None = None


class FillMissingOperation(LearningInput):
    op: Literal['fill_missing']
    column: Name
    value: Cell

    @field_validator('value', mode='before')
    @classmethod
    def blank_cell(cls, value):
        return '' if value is None else value


class FilterOperation(LearningInput):
    op: Literal['filter']
    column: Name
    operator: Literal['eq', 'ne', 'gt', 'gte', 'lt', 'lte', 'contains', 'is_empty', 'not_empty']
    value: Cell = None
    numeric: bool = False

    @field_validator('value', mode='before')
    @classmethod
    def blank_cell(cls, value):
        return '' if value is None else value

    @model_validator(mode='after')
    def comparison_value(self):
        if self.operator not in ('is_empty', 'not_empty') and 'value' not in self.model_fields_set:
            raise ValueError('filter requires value for this operator')
        return self


class SortOperation(LearningInput):
    op: Literal['sort']
    columns: NonemptyNames
    ascending: bool = True
    numeric: bool = False


class SelectOperation(LearningInput):
    op: Literal['select']
    columns: NonemptyNames


class RenameOperation(LearningInput):
    op: Literal['rename']
    mapping: dict[Name, Name] = Field(min_length=1, max_length=100, description='Existing column name to new column name.')


class Aggregation(LearningInput):
    column: Name
    op: Literal['sum', 'mean', 'min', 'max', 'count']
    output: Name


class GroupByOperation(LearningInput):
    op: Literal['groupby']
    by: Names
    aggregations: Annotated[list[Aggregation], Field(min_length=1, max_length=100)]


class DeriveOperation(LearningInput):
    op: Literal['derive']
    column: Name = Field(description='New output column; cannot overwrite an existing column.')
    operator: Literal['add', 'subtract', 'multiply', 'divide']
    left: ColumnOperand | ValueOperand
    right: ColumnOperand | ValueOperand


class UpdateOperation(LearningInput):
    op: Literal['update']
    row: Annotated[int, Field(ge=0)] = Field(description='Zero-based data row, excluding header.')
    column: Name
    expected: Cell = Field(description='Exact prior value; change fails if it differs.')
    value: Cell

    @field_validator('expected', 'value', mode='before')
    @classmethod
    def blank_cell(cls, value):
        return '' if value is None else value


LearningOperation = Annotated[Union[
    DropDuplicatesOperation, FillMissingOperation, FilterOperation, SortOperation,
    SelectOperation, RenameOperation, GroupByOperation, DeriveOperation, UpdateOperation,
], Field(discriminator='op')]


class LearningRecipe(LearningInput):
    inputColumns: NonemptyNames = Field(description='Expected input header names. Every declared input cell is nonempty by default.')
    optionalInputColumns: Names = Field(default_factory=list, description='Explicitly approved exceptions allowed to be empty; subset of inputColumns. Never add an exception merely to bypass missing price/date validation.')
    allowExtraColumns: bool = False
    sheetName: Annotated[str, Field(min_length=1, max_length=31)] | None = Field(default=None, description='INPUT XLSX worksheet selector only. Omit for one nonempty sheet. Output XLSX sheet is always Result; do not copy the output sample tab here.')
    mapping: dict[Name, Name] = Field(min_length=1, max_length=100, description='OUTPUT column name to INPUT column name, before bounded operations.')
    constants: dict[Name, Cell] = Field(default_factory=dict, max_length=100)
    concat: Annotated[list[LearningConcat], Field(max_length=20)] = Field(default_factory=list)
    operations: Annotated[list[LearningOperation], Field(max_length=30)] = Field(default_factory=list, description='Only these bounded table operations; no expressions, code, network or shell.')
    outputColumns: NonemptyNames = Field(description='Final ordered output columns.')
    requiredValues: Names = Field(description='Output columns that must be nonempty, in addition to input checks.')
    numericColumns: Names
    moneyColumns: Names = Field(default_factory=list, description='Subset of numericColumns. Requires explicit commercial basis.')
    totals: Names = Field(default_factory=list, description='Optional numeric totals; not required unless user requests them.')
    preserveRowCount: bool = True
    commercial: LearningCommercial | None = None
    outputFormat: Literal['csv', 'xlsx']


class LearningProposal(LearningInput):
    title: Name = Field(description='Method title. For a revision preserve the prior title exactly.')
    kind: Literal['note', 'table_workflow']
    scope: LearningScope
    instruction: Annotated[str, Field(min_length=1, max_length=20000)] = Field(description='User-taught reusable method or preference, not one-order facts generalized into permanent commercial terms. Original user teaching is captured by the server; do not supply a teaching field.')
    sources: Annotated[list[LearningSource], Field(max_length=12)]
    parentId: Annotated[str, Field(pattern=r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$')] | None = Field(default=None, description='Revision only: VERSION UUID from get_learning.id/search_learning.methods[].id: current approved version, its unapproved correction draft, initial draft, or most recently disabled version when none is active. NOT scopeId, profileId, versionHash or a source ID. Omit on initial proposal. Do not copy server reviewBase metadata.')
    recipe: LearningRecipe | None = None
    exampleFileId: str | None = Field(default=None, min_length=1, description='Required for table_workflow: existing customer input file to dry-run, not the completed sample.')
    expectedOutputFileId: str | None = Field(default=None, min_length=1, description='Optional user-selected completed reference; full output must match its cells. It may originate from an internal colleague.')

    @model_validator(mode='after')
    def workflow_fields(self):
        if self.kind == 'table_workflow' and (self.recipe is None or self.exampleFileId is None):
            raise ValueError('table_workflow requires recipe and exampleFileId')
        if self.kind == 'note' and any(v is not None for v in (self.recipe, self.exampleFileId, self.expectedOutputFileId)):
            raise ValueError('note must omit recipe, exampleFileId and expectedOutputFileId')
        return self
