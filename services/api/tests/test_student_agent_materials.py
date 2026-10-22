"""Host-only material proposals never add files to selector model context."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from navox.ai.catalog import catalog_template
from navox.student_agent.contracts import (
    MANIFEST_HASH,
    Observation,
    SelectorOutput,
    SelectorRequest,
    ToolSelection,
)
from navox.student_agent.selector import SELECTOR_PROMPT, SELECTOR_SCHEMA, validate_selection

# Parametrized questions include these values in shard collection node IDs.
COURSE = "55555555-5555-4555-8555-555555555555"
MATERIAL = "66666666-6666-4666-8666-666666666666"


def request(question, observations=()):
    return SelectorRequest(
        session_id=uuid4(),
        turn_id=uuid4(),
        step=1,
        question=question,
        manifest_hash=MANIFEST_HASH,
        observations=observations,
    )


def search(query="hash tables", course_id=COURSE):
    return SelectorOutput(
        selection=ToolSelection(
            kind="tool",
            name="course.materials.search",
            args={"course_id": course_id, "query": query},
        )
    )


def course_observation(*courses):
    return Observation(
        call_id=uuid4(),
        name="course.info",
        effect="read",
        data={
            "courses": [
                {
                    "id": identifier,
                    "code": code,
                    "name": name,
                    "professor": None,
                    "term_start": "2026-08-20",
                    "term_end": "2026-12-20",
                }
                for identifier, code, name in courses
            ],
            "truncated": False,
        },
    )


@pytest.mark.parametrize("name", ["course.materials.search", "course.materials.page"])
def test_material_results_are_rejected_even_when_well_formed(name):
    data = (
        {
            "course_id": COURSE,
            "query": "hash tables",
            "results": [
                {
                    "material_id": MATERIAL,
                    "revision": 1,
                    "title": "Lecture notes",
                    "page": 1,
                    "snippet": "Private source words about hash tables.",
                }
            ],
            "limit_reached": False,
        }
        if name.endswith("search")
        else {
            "course_id": COURSE,
            "material_id": MATERIAL,
            "revision": 1,
            "title": "Lecture notes",
            "page": 1,
            "text": "Private source text.",
            "truncated": False,
        }
    )
    raw = {"call_id": uuid4(), "name": name, "effect": "read", "data": data}
    with pytest.raises(ValidationError):
        Observation.model_validate(raw)
    with pytest.raises(ValidationError):
        request("Search my course notes", (raw,))


def test_material_text_cannot_be_disguised_as_metadata():
    with pytest.raises(ValidationError):
        Observation(
            call_id=uuid4(),
            name="course.info",
            effect="read",
            data={"courses": [], "truncated": False, "material_text": "Private source"},
        )


def test_selector_material_proposals_require_new_unpublished_v2_artifacts():
    assert SELECTOR_PROMPT.name == SELECTOR_SCHEMA.name == "student_tool_selector"
    assert SELECTOR_PROMPT.version == SELECTOR_SCHEMA.version == "v2"
    registry = catalog_template()
    assert all(item.reference != SELECTOR_PROMPT for item in registry.prompts)
    assert all(item.reference != SELECTOR_SCHEMA for item in registry.schemas)


@pytest.mark.parametrize("query", ["", "...", " ", "one two three four five six seven eight nine"])
def test_material_search_requires_one_to_eight_actual_words(query):
    with pytest.raises(ValidationError):
        search(query)


@pytest.mark.parametrize(
    "query", ["hash", "hash tables", "one two three four five six seven eight"]
)
def test_material_search_accepts_bounded_literal_terms(query):
    validate_selection(search(query), request(f"Search course {COURSE} for {query}"))


@pytest.mark.parametrize("query", ["secret", "hash secrets", "cat"])
def test_material_search_never_invents_terms_or_matches_only_word_fragments(query):
    with pytest.raises(ValueError):
        validate_selection(search(query), request(f"Search course {COURSE} for hash education"))


def test_material_search_requires_explicit_course_even_if_one_course_is_observed():
    observation = course_observation((COURSE, "CS 2110", "Programming"))
    with pytest.raises(ValueError):
        validate_selection(search(), request("Search my notes for hash tables", (observation,)))


@pytest.mark.parametrize("label", ["CS 2110", "CS2110", "Programming"])
def test_material_search_resolves_a_user_named_observed_course(label):
    observation = course_observation((COURSE, "CS 2110", "Programming"))
    validate_selection(search(), request(f"Search {label} notes for hash tables", (observation,)))


def test_material_search_rejects_ambiguous_matching_course_labels():
    observation = course_observation(
        (COURSE, "CS 2110", "Programming"),
        (str(uuid4()), "CS 2110", "Programming"),
    )
    with pytest.raises(ValueError):
        validate_selection(
            search(), request("Search CS 2110 notes for hash tables", (observation,))
        )


def test_material_search_rejects_an_unobserved_course_name():
    with pytest.raises(ValueError):
        validate_selection(search(), request("Search CS 2110 notes for hash tables"))


def page_selection():
    return SelectorOutput(
        selection=ToolSelection(
            kind="tool",
            name="course.materials.page",
            args={"course_id": COURSE, "material_id": MATERIAL, "revision": 2, "page": 3},
        )
    )


def test_material_page_requires_the_user_supplied_exact_material_version_and_page():
    validate_selection(
        page_selection(),
        request(f"Read course {COURSE} material {MATERIAL} version 2 page 3"),
    )


@pytest.mark.parametrize(
    "question",
    [
        f"Read course {COURSE} version 2 page 3",
        f"Read course {COURSE} material {MATERIAL} page 3",
        f"Read course {COURSE} material {MATERIAL} version 2",
        f"Read course {COURSE} material {MATERIAL} version 1 page 3",
        f"Read course {COURSE} material {MATERIAL} version 2 page 4",
    ],
)
def test_material_page_cannot_fill_in_missing_or_changed_identifiers(question):
    with pytest.raises(ValueError):
        validate_selection(page_selection(), request(question))
