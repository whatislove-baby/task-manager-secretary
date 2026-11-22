"""Model-authored skill proposals; audit/review fields are server-owned."""
from typing import Annotated, Literal
from pydantic import Field, model_validator
from .learning_schema import LearningInput, LearningScope, LearningSource, Name


class SkillInput(LearningInput):
    name: Annotated[str, Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$')]
    description: Annotated[str, Field(min_length=1, max_length=1000)]


class SkillOutput(SkillInput):
    comparison: Literal['table', 'workbook', 'text', 'bytes'] = Field(description='workbook compares XLSX sheet order, exact values/types, formulas and template styles/layout (ignores ZIP timestamps/creator and unverified formula caches); use for fixed workbook templates. table compares literal CSV/XLSX table text only; text normalizes UTF-8 newlines; bytes requires exact file bytes. None claims rendered visual review.')


class SkillPackageImport(LearningInput):
    fileId: Annotated[str,Field(min_length=1)]
    scope: LearningScope
    instruction: Annotated[str,Field(max_length=4000)]=''
    parentId: str | None=None


class SkillExample(LearningInput):
    name: Name
    kind: Literal['success', 'failure']
    inputFiles: dict[str, str] = Field(min_length=1, max_length=8, description='Declared input names to managed file IDs, never paths.')
    expectedFiles: dict[str, str] = Field(default_factory=dict, max_length=5, description='For success: every declared output name to an independently user-provided completed file ID. Agent-generated files cannot be test oracles.')
    expectedError: Annotated[str, Field(min_length=1, max_length=300)] | None = Field(default=None, description='For failure: precise validation message expected with exit code 2 and no output files; generic crash/timeout is not an expected rejection.')


class SkillProposal(LearningInput):
    title: Name
    kind: Literal['memory', 'script', 'hybrid']
    scope: LearningScope
    rationale: Annotated[str, Field(min_length=1, max_length=4000)] = Field(description='Explain why this is reusable experience, deterministic code, or both. A one-order date/price is an event, not a permanent skill. Prefer existing tools when sufficient.')
    instruction: Annotated[str, Field(min_length=1, max_length=20000)] = Field(description='Markdown body: trigger, user-taught rules, inputs/outputs, exceptions and invocation. Saved in SKILL.md; do not include YAML frontmatter.')
    sources: Annotated[list[LearningSource], Field(max_length=20)] = Field(default_factory=list)
    parentId: str | None = Field(default=None, description='Version UUID for a correction; preserve title and exact scope. Not scopeId/hash.')
    code: Annotated[str, Field(min_length=1, max_length=64000)] | None = Field(default=None, description='Complete Python main.py. Read named files under os.environ[TM_SKILL_INPUT_DIR], write only declared output filenames under TM_SKILL_OUTPUT_DIR. Use installed libraries, no pip/network. For invalid business input print the precise reason to stderr and exit(2), before writing any outputs.')
    inputs: Annotated[list[SkillInput], Field(max_length=8)] = Field(default_factory=list)
    outputs: Annotated[list[SkillOutput], Field(max_length=5)] = Field(default_factory=list)
    examples: Annotated[list[SkillExample], Field(max_length=10)] = Field(default_factory=list)

    @model_validator(mode='after')
    def kind_fields(self):
        if self.kind == 'memory' and (self.code is not None or self.inputs or self.outputs or self.examples):
            raise ValueError('memory saves Markdown only; omit code/inputs/outputs/examples')
        if self.kind != 'memory' and (self.code is None or not self.inputs or not self.outputs):
            raise ValueError('script/hybrid require code and explicit inputs/outputs')
        for values in (self.inputs,self.outputs):
            names=[v.name for v in values]
            if len(names)!=len(set(names)):raise ValueError('contract filenames must be unique')
        return self
