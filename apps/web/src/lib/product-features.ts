/** Student launch keeps optional products available only by deployment choice. */
export const productFeatures = {
  news: process.env.NEXT_PUBLIC_NAVOX_NEWS_ENABLED === "true",
  subscriptions: process.env.NEXT_PUBLIC_NAVOX_SUBSCRIPTIONS_ENABLED === "true",
};
