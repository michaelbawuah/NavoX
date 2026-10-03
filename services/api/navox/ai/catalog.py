"""A reviewable empty catalog: no guessed model IDs, prices, grants, or traffic."""

from navox.ai.foundation.contracts import Capability, Profile, TaskType
from navox.ai.foundation.registry import ProfileDefinition, RegistrySnapshot
from navox.ai.prompts import builtin_prompts


def catalog_template() -> RegistrySnapshot:
    prompts, schemas = builtin_prompts()
    tasks = {
        Profile.EMBEDDING: {TaskType.EMBED},
        Profile.EXTRACTION_FAST: {TaskType.EXTRACT, TaskType.CLASSIFY},
        Profile.EXTRACTION_HIGH_ACCURACY: {TaskType.EXTRACT},
        Profile.REASONING_STANDARD: {TaskType.REASON, TaskType.RANK},
        Profile.REASONING_HIGH: {TaskType.REASON},
        Profile.PLANNING_HIGH: {TaskType.PLAN},
        Profile.SUMMARIZATION_FAST: {TaskType.SUMMARIZE},
        Profile.MULTIMODAL_STANDARD: {TaskType.EXTRACT, TaskType.REASON},
        Profile.NEWS_SYNTHESIS: {TaskType.SUMMARIZE, TaskType.REASON},
        Profile.CODE_REASONING: {TaskType.REASON},
        Profile.ASSISTANT_INTERACTIVE: {TaskType.REASON, TaskType.DRAFT_COMMUNICATION},
        Profile.ASSISTANT_HIGH_REASONING: {TaskType.REASON, TaskType.PLAN},
    }
    return RegistrySnapshot(
        revision=1,
        prompts=prompts,
        schemas=schemas,
        profiles=tuple(
            ProfileDefinition(
                profile=profile,
                task_types=frozenset(tasks[profile]),
                required_capabilities=(
                    frozenset({Capability.EMBEDDINGS})
                    if profile == Profile.EMBEDDING
                    else frozenset({Capability.TEXT, Capability.STRUCTURED_OUTPUT})
                )
                | (
                    frozenset({Capability.VISION})
                    if profile == Profile.MULTIMODAL_STANDARD
                    else frozenset()
                ),
            )
            # Only profiles with a declared task binding are published; vocabulary
            # added for audio qualification stays absent until an operator opts in.
            for profile in tasks
        ),
    )
