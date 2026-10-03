"""Immutable, explicitly configured registry snapshots; no automatic model enabling."""

from __future__ import annotations

import json
from typing import Annotated, Self

from pydantic import Field, model_validator

from navox.ai.foundation.contracts import (
    AITask,
    AudioUnitPrice,
    Capability,
    Contract,
    Identifier,
    JSONDocument,
    Money,
    Profile,
    Provider,
    Sensitivity,
    TaskType,
    VersionedRef,
)


class ModelRef(Contract):
    provider: Provider
    model: Identifier


class ModelDefinition(Contract):
    reference: ModelRef
    capabilities: frozenset[Capability] = Field(min_length=1)
    allowed_sensitivities: frozenset[Sensitivity] = Field(default_factory=frozenset)
    enabled: Annotated[bool, Field(strict=True)] = False
    context_window: Annotated[int, Field(strict=True, ge=1)]
    max_output_tokens: Annotated[int, Field(strict=True, ge=1)]
    input_cost_per_million: Money | None = None
    output_cost_per_million: Money | None = None
    # Audio prices stay omitted while unset so historical snapshot JSON and its
    # digest remain byte-identical for every registered text/embedding model.
    transcription_cost_per_minute: AudioUnitPrice | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    synthesis_cost_per_1000_characters: AudioUnitPrice | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def check_token_limits(self) -> Self:
        if self.max_output_tokens > self.context_window:
            raise ValueError("Output token limit exceeds configured context window")
        return self


class ProfileDefinition(Contract):
    profile: Profile
    task_types: frozenset[TaskType] = Field(min_length=1)
    required_capabilities: frozenset[Capability] = Field(min_length=1)
    assignments: tuple[ModelRef, ...] = ()

    @model_validator(mode="after")
    def check_assignments(self) -> Self:
        if len(set(self.assignments)) != len(self.assignments):
            raise ValueError("Duplicate profile model assignment")
        return self


class SchemaDefinition(Contract):
    reference: VersionedRef
    document: JSONDocument = Field(repr=False)

    @model_validator(mode="after")
    def check_schema_document(self) -> Self:
        # Syntax only. A schema compiler and domain validator are required in M5.
        if not isinstance(json.loads(self.document.text), dict):
            raise ValueError("Schema document must be a JSON object")
        return self


class PromptDefinition(Contract):
    reference: VersionedRef
    output_schema: VersionedRef
    instructions: Annotated[str, Field(min_length=1, max_length=100_000, repr=False)]

    @model_validator(mode="after")
    def check_instructions(self) -> Self:
        if not self.instructions.strip():
            raise ValueError("Prompt instructions cannot be blank")
        return self


class TaskBinding(Contract):
    profile: ProfileDefinition
    prompt: PromptDefinition = Field(repr=False)
    output_schema: SchemaDefinition = Field(repr=False)


class RegistrySnapshot(Contract):
    """In-memory configuration value, not a database, router, or authorization store."""

    revision: Annotated[int, Field(strict=True, ge=1)]
    models: tuple[ModelDefinition, ...] = ()
    profiles: tuple[ProfileDefinition, ...] = ()
    schemas: tuple[SchemaDefinition, ...] = ()
    prompts: tuple[PromptDefinition, ...] = Field(default=(), repr=False)

    @model_validator(mode="after")
    def check_links(self) -> Self:
        models = {item.reference: item for item in self.models}
        profiles = {item.profile for item in self.profiles}
        schemas = {item.reference for item in self.schemas}
        prompts = {item.reference for item in self.prompts}
        if len(models) != len(self.models):
            raise ValueError("Duplicate model registration")
        if len(profiles) != len(self.profiles):
            raise ValueError("Duplicate profile registration")
        if len(schemas) != len(self.schemas):
            raise ValueError("Duplicate schema version")
        if len(prompts) != len(self.prompts):
            raise ValueError("Duplicate prompt version")
        for prompt in self.prompts:
            if prompt.output_schema not in schemas:
                raise ValueError("Prompt references an unregistered schema version")
        for profile in self.profiles:
            for reference in profile.assignments:
                model = models.get(reference)
                if model is None:
                    raise ValueError("Profile references an unregistered model")
                if not profile.required_capabilities.issubset(model.capabilities):
                    raise ValueError("Assigned model lacks a required profile capability")
        return self

    def get_model(self, reference: ModelRef) -> ModelDefinition:
        for item in self.models:
            if item.reference == reference:
                return item
        raise LookupError("Model not registered")

    def bind_task(self, task: AITask) -> TaskBinding:
        """Resolve exact versions, never latest. Selection/validation remain separate."""

        profile = next((item for item in self.profiles if item.profile == task.profile), None)
        prompt = next((item for item in self.prompts if item.reference == task.prompt), None)
        schema = next((item for item in self.schemas if item.reference == task.output_schema), None)
        if profile is None or prompt is None or schema is None:
            raise LookupError("Task references unregistered configuration")
        if task.task_type not in profile.task_types:
            raise ValueError("Task type is incompatible with profile")
        if not profile.required_capabilities.issubset(task.capability_requirements):
            raise ValueError("Task cannot drop required profile capabilities")
        if prompt.output_schema != schema.reference:
            raise ValueError("Task prompt and output schema versions disagree")
        return TaskBinding(profile=profile, prompt=prompt, output_schema=schema)


def validate_registry_update(previous: RegistrySnapshot, proposed: RegistrySnapshot) -> None:
    """Preserve historical prompt/schema content; model configuration may change."""

    if proposed.revision <= previous.revision:
        raise ValueError("Registry revision must increase")
    new_prompts = {item.reference: item for item in proposed.prompts}
    new_schemas = {item.reference: item for item in proposed.schemas}
    for prompt in previous.prompts:
        if new_prompts.get(prompt.reference) != prompt:
            raise ValueError("Published prompt versions cannot be removed or replaced")
    for schema in previous.schemas:
        if new_schemas.get(schema.reference) != schema:
            raise ValueError("Published schema versions cannot be removed or replaced")
