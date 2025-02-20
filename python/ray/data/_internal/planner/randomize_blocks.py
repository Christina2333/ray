from typing import Callable, List, Optional, Tuple

from ray.data._internal.execution.interfaces import RefBundle, TaskContext
from ray.data._internal.stats import StatsDict


def generate_randomize_blocks_fn(
    seed: Optional[int],
) -> Callable[[List[RefBundle], TaskContext], Tuple[List[RefBundle], StatsDict]]:
    """Generate function to randomize order of blocks."""

    def fn(
        refs: List[RefBundle], context: TaskContext
    ) -> Tuple[List[RefBundle], StatsDict]:
        import random

        blocks_with_metadata = []
        for ref_bundle in refs:
            for block, meta in ref_bundle.blocks:
                blocks_with_metadata.append((block, meta))

        if len(blocks_with_metadata) == 0:
            return refs, {}
        else:
            if seed is not None:
                random.seed(seed)
            input_owned = all(b.owns_blocks for b in refs)
            random.shuffle(blocks_with_metadata)
            output = []
            for block, meta in blocks_with_metadata:
                output.append(
                    RefBundle(
                        [
                            (
                                block,
                                meta,
                            )
                        ],
                        owns_blocks=input_owned,
                    )
                )
            return output, {}

    return fn
