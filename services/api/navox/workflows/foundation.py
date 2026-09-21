from temporalio import workflow


@workflow.defn
class FoundationHeartbeatWorkflow:
    """Minimal deterministic workflow proving the worker boundary is wired."""

    @workflow.run
    async def run(self, workspace_id: str) -> str:
        return f"NavoX foundation ready for workspace {workspace_id}"
