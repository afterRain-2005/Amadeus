import os
import uuid

import pytest

from core.single_instance import acquire_single_instance, release_single_instance


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
