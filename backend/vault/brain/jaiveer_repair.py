"""Dispatch: max_concurrent, per_node, token bucket; execute repair/move/trim via node /pull and DELETE; retries. §4.8. Task J7.
Owner: Jaiveer. Entry point for loops: async def run(ctx: BrainContext) -> None
"""
