/** Student launch keeps optional products available only by deployment choice. */
export const productFeatures = {
  studentEmail: process.env.NEXT_PUBLIC_NAVOX_STUDENT_EMAIL_ENABLED === "true",
  studentAgent: process.env.NEXT_PUBLIC_NAVOX_STUDENT_AGENT_ENABLED === "true",
  studentAgentToolContext:
    process.env.NEXT_PUBLIC_NAVOX_STUDENT_AGENT_TOOL_CONTEXT_ENABLED === "true",
  news: process.env.NEXT_PUBLIC_NAVOX_NEWS_ENABLED === "true",
  subscriptions: process.env.NEXT_PUBLIC_NAVOX_SUBSCRIPTIONS_ENABLED === "true",
  studentPlanning:
    process.env.NEXT_PUBLIC_NAVOX_STUDENT_PLANNING_ENABLED === "true",
  studentLmsFeeds:
    process.env.NEXT_PUBLIC_NAVOX_STUDENT_LMS_FEEDS_ENABLED === "true",
  studentCoursework:
    process.env.NEXT_PUBLIC_NAVOX_STUDENT_COURSEWORK_ENABLED === "true",
  studentRecovery:
    process.env.NEXT_PUBLIC_NAVOX_STUDENT_RECOVERY_ENABLED === "true",
};
