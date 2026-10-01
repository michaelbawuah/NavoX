import asyncio

from temporalio.client import Client
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.worker import Worker

from navox.agent.activities import execute_plan_step_activity, finalize_plan_activity
from navox.approvals.activities import (
    action_authorization_state_activity,
    execute_approved_action_activity,
    mark_execution_uncertain_activity,
)
from navox.connectors.activities import (
    connector_disconnect_activity,
    connector_event_reconciliation_activity,
    connector_health_activity,
    connector_reconciliation_activity,
    connector_subscription_activity,
    connector_subscription_reconciliation_activity,
    connector_sync_activity,
    legacy_google_watch_cleanup_activity,
)
from navox.core.settings import get_settings
from navox.intelligence.activities import (
    intelligence_workspaces_activity,
    pending_intelligence_activity,
    process_source_activity,
    refresh_intelligence_activity,
)
from navox.knowledge.activities import (
    knowledge_delete_activity,
    knowledge_embedding_activity,
    knowledge_pending_embeddings_activity,
    knowledge_reindex_activity,
    knowledge_resource_activity,
    knowledge_source_page_activity,
)
from navox.news.activities import (
    ingest_news_source_activity,
    news_clustering_activity,
    news_conversation_activity,
    news_exact_index_activity,
    news_intelligence_activity,
    news_sources_activity,
)
from navox.proactive.activities import (
    commitment_timing_state_activity,
    daily_briefing_delay_activity,
    evaluate_proactive_workspace_activity,
    mark_proactive_workflow_status_activity,
    prepare_meeting_activity,
    record_scheduled_briefing_activity,
)
from navox.subscriptions.activities import (
    cancellation_state_activity,
    execute_cancellation_activity,
    pending_subscriptions_activity,
    reevaluate_subscription_activity,
    verify_cancellation_activity,
)
from navox.workflows.approved_action import ApprovedActionWorkflow
from navox.workflows.connectors import (
    ConnectorDisconnectWorkflow,
    ConnectorHealthWorkflow,
    ConnectorIncrementalSyncWorkflow,
    ConnectorInitialSyncWorkflow,
    ConnectorReconciliationWorkflow,
    ConnectorSubscriptionWorkflow,
)
from navox.workflows.foundation import FoundationHeartbeatWorkflow
from navox.workflows.handle_commitment import HandleCommitmentWorkflow
from navox.workflows.intelligence import (
    AttentionEvaluationWorkflow,
    FeedbackLearningWorkflow,
    IntelligenceReconciliationWorkflow,
    ProcessSourceEventWorkflow,
    ReevaluateCommitmentWorkflow,
    TodayRefreshWorkflow,
)
from navox.workflows.knowledge import (
    KnowledgeEmbeddingWorkflow,
    KnowledgeEntityResolutionWorkflow,
    KnowledgePermissionRefreshWorkflow,
    KnowledgeReconciliationWorkflow,
    KnowledgeReindexWorkflow,
    KnowledgeResourceDeleteWorkflow,
    KnowledgeResourceIngestWorkflow,
    KnowledgeResourceUpdateWorkflow,
)
from navox.workflows.news import (
    NewsConversationRefreshWorkflow,
    NewsSourceIngestionWorkflow,
    NewsSourceReconciliationWorkflow,
)
from navox.workflows.proactive import (
    CommitmentLifecycleWorkflow,
    DailyBriefingWorkflow,
    FollowUpWorkflow,
    MeetingPreparationWorkflow,
)
from navox.workflows.subscriptions import (
    CancellationContradictionWorkflow,
    CancelSubscriptionWorkflow,
    DiscoverRecurringObligationWorkflow,
    PriceChangeWorkflow,
    ReevaluateRecurringObligationWorkflow,
    RenewalLifecycleWorkflow,
    SubscriptionReconciliationWorkflow,
    TrialLifecycleWorkflow,
    VerifyCancellationWorkflow,
)


async def main() -> None:
    settings = get_settings()
    client = await Client.connect(settings.temporal_target)
    worker = Worker(
        client,
        task_queue=settings.temporal_task_queue,
        workflows=[
            KnowledgeResourceIngestWorkflow,
            KnowledgeResourceUpdateWorkflow,
            KnowledgeResourceDeleteWorkflow,
            KnowledgeEmbeddingWorkflow,
            KnowledgeEntityResolutionWorkflow,
            KnowledgePermissionRefreshWorkflow,
            KnowledgeReindexWorkflow,
            KnowledgeReconciliationWorkflow,
            NewsConversationRefreshWorkflow,
            NewsSourceIngestionWorkflow,
            NewsSourceReconciliationWorkflow,
            DiscoverRecurringObligationWorkflow,
            ReevaluateRecurringObligationWorkflow,
            RenewalLifecycleWorkflow,
            TrialLifecycleWorkflow,
            PriceChangeWorkflow,
            CancelSubscriptionWorkflow,
            VerifyCancellationWorkflow,
            CancellationContradictionWorkflow,
            SubscriptionReconciliationWorkflow,
            ConnectorInitialSyncWorkflow,
            ConnectorIncrementalSyncWorkflow,
            ConnectorReconciliationWorkflow,
            ConnectorSubscriptionWorkflow,
            ConnectorHealthWorkflow,
            ConnectorDisconnectWorkflow,
            ProcessSourceEventWorkflow,
            ReevaluateCommitmentWorkflow,
            AttentionEvaluationWorkflow,
            TodayRefreshWorkflow,
            FeedbackLearningWorkflow,
            IntelligenceReconciliationWorkflow,
            FoundationHeartbeatWorkflow,
            HandleCommitmentWorkflow,
            ApprovedActionWorkflow,
            CommitmentLifecycleWorkflow,
            FollowUpWorkflow,
            MeetingPreparationWorkflow,
            DailyBriefingWorkflow,
        ],
        activities=[
            knowledge_delete_activity,
            knowledge_embedding_activity,
            knowledge_reindex_activity,
            knowledge_resource_activity,
            knowledge_pending_embeddings_activity,
            knowledge_source_page_activity,
            news_clustering_activity,
            news_conversation_activity,
            news_exact_index_activity,
            news_intelligence_activity,
            news_sources_activity,
            ingest_news_source_activity,
            reevaluate_subscription_activity,
            cancellation_state_activity,
            execute_cancellation_activity,
            verify_cancellation_activity,
            pending_subscriptions_activity,
            connector_sync_activity,
            connector_health_activity,
            connector_reconciliation_activity,
            connector_subscription_activity,
            connector_subscription_reconciliation_activity,
            connector_event_reconciliation_activity,
            connector_disconnect_activity,
            legacy_google_watch_cleanup_activity,
            intelligence_workspaces_activity,
            process_source_activity,
            refresh_intelligence_activity,
            pending_intelligence_activity,
            execute_plan_step_activity,
            finalize_plan_activity,
            action_authorization_state_activity,
            execute_approved_action_activity,
            mark_execution_uncertain_activity,
            evaluate_proactive_workspace_activity,
            commitment_timing_state_activity,
            mark_proactive_workflow_status_activity,
            prepare_meeting_activity,
            daily_briefing_delay_activity,
            record_scheduled_briefing_activity,
        ],
    )
    async with worker:
        # Cleanup continues when connected search is disabled; indexing stays gated.
        try:
            await client.start_workflow(
                KnowledgeReconciliationWorkflow.run,
                id="navox-knowledge-reconciliation-v1",
                task_queue=settings.temporal_task_queue,
            )
        except WorkflowAlreadyStartedError:
            pass
        # Expiry/revocation cleanup continues even when the News feature is disabled.
        try:
            await client.start_workflow(
                NewsSourceReconciliationWorkflow.run,
                id="navox-news-source-reconciliation-v1",
                task_queue=settings.temporal_task_queue,
            )
        except WorkflowAlreadyStartedError:
            pass
        # Manual lifecycle facts and confirmed cancellation recovery work without AI.
        try:
            await client.start_workflow(
                SubscriptionReconciliationWorkflow.run,
                id="navox-subscription-reconciliation-v1",
                task_queue=settings.temporal_task_queue,
            )
        except WorkflowAlreadyStartedError:
            pass
        if settings.ai_provider != "disabled":
            for workflow_run, workflow_id in (
                (
                    IntelligenceReconciliationWorkflow.run,
                    "navox-intelligence-reconciliation-v1",
                ),
                (
                    ConnectorReconciliationWorkflow.run,
                    "navox-connector-reconciliation-v1",
                ),
            ):
                try:
                    await client.start_workflow(
                        workflow_run,
                        id=workflow_id,
                        task_queue=settings.temporal_task_queue,
                    )
                except WorkflowAlreadyStartedError:
                    pass
        await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
