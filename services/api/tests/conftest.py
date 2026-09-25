"""Operation counters attached to JUnit evidence, independent of test pass counts."""

import os

import pytest

from navox.evaluation.connector_metrics import PROPERTY, Measurement


@pytest.fixture
def measure(request):
    def record(metric, numerator, denominator, scenario, *, database=False):
        engine = "in_memory"
        if isinstance(database, str):
            engine = database
        elif database:
            engine = (
                "postgresql"
                if os.environ.get("NAVOX_CONNECTOR_TEST_DSN", "").startswith("postgresql")
                else "sqlite"
            )
        sample = Measurement(
            metric=metric,
            numerator=numerator,
            denominator=denominator,
            scenario=scenario,
            environment=engine,
        )
        request.node.user_properties.append((PROPERTY, sample.model_dump_json()))

    return record
