/** Student launch keeps optional products available only by deployment choice. */
export const productFeatures = {
  studentVox: process.env.NEXT_PUBLIC_NAVOX_STUDENT_VOX_ENABLED === "true",
  studentWhiteboard:
    process.env.NEXT_PUBLIC_NAVOX_STUDENT_WHITEBOARD_ENABLED === "true",
  studentCoach: process.env.NEXT_PUBLIC_NAVOX_STUDENT_COACH_ENABLED === "true",
  studentWorkExport:
    process.env.NEXT_PUBLIC_NAVOX_STUDENT_WORK_EXPORT_ENABLED === "true",
  studentGoals: process.env.NEXT_PUBLIC_NAVOX_STUDENT_GOALS_ENABLED === "true",
  studentEmail: process.env.NEXT_PUBLIC_NAVOX_STUDENT_EMAIL_ENABLED === "true",
  studentAgent: process.env.NEXT_PUBLIC_NAVOX_STUDENT_AGENT_ENABLED === "true",
  studentAgentToolContext:
    process.env.NEXT_PUBLIC_NAVOX_STUDENT_AGENT_TOOL_CONTEXT_ENABLED === "true",
  news: process.env.NEXT_PUBLIC_NAVOX_NEWS_ENABLED === "true",
  subscriptions: process.env.NEXT_PUBLIC_NAVOX_SUBSCRIPTIONS_ENABLED === "true",
  studentPlanning:
    process.env.NEXT_PUBLIC_NAVOX_STUDENT_PLANNING_ENABLED === "true",
  studentCalendarAvailability:
    process.env.NEXT_PUBLIC_NAVOX_STUDENT_CALENDAR_AVAILABILITY_ENABLED ===
    "true",
  studentCalendarWrites:
    process.env.NEXT_PUBLIC_NAVOX_STUDENT_CALENDAR_WRITES_ENABLED === "true",
  studentLmsFeeds:
    process.env.NEXT_PUBLIC_NAVOX_STUDENT_LMS_FEEDS_ENABLED === "true",
  studentCoursework:
    process.env.NEXT_PUBLIC_NAVOX_STUDENT_COURSEWORK_ENABLED === "true",
  studentRecovery:
    process.env.NEXT_PUBLIC_NAVOX_STUDENT_RECOVERY_ENABLED === "true",
};
