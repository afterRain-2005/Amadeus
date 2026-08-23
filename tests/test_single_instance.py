import os
import uuid

import pytest

from core.single_instance import (
    acquire_single_instance,
    consume_instance_signal,
    create_instance_signal,
    release_instance_signal,
    release_single_instance,
    signal_existing_instance,
)


@pytest.mark.skipif(os.name != "nt", reason="Windows named mutex test")
def test_named_mutex_blocks_second_acquire_then_releases():
    name = f"Amadeus2026.Test.{uuid.uuid4()}"
    assert acquire_single_instance(name) is True
    try:
        assert acquire_single_instance(name) is False
    finally:
        release_single_instance(name)

    assert acquire_single_instance(name) is True
    release_single_instance(name)


@pytest.mark.skipif(os.name != "nt", reason="Windows named event test")
def test_activation_signal_is_consumed_once():
    name = f"Amadeus2026.Test.{uuid.uuid4()}"
    create_instance_signal(name)
    try:
        assert consume_instance_signal(name) is False
        assert signal_existing_instance(name) is True
        assert consume_instance_signal(name) is True
        assert consume_instance_signal(name) is False
    finally:
        release_instance_signal(name)
