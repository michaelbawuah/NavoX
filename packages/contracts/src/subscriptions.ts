/** SPEC-004: public, credential-free subscription and exact approval contracts. */
export type SubscriptionInterval = "DAY" | "WEEK" | "MONTH" | "QUARTER" | "YEAR" | "UNKNOWN";
export type SubscriptionStatus = "CANDIDATE" | "TRIAL" | "ACTIVE" | "PAUSED" | "CANCELLATION_REQUESTED" | "CANCEL_PENDING" | "CANCELLED" | "EXPIRED" | "UNKNOWN";
export type SubscriptionType = "SUBSCRIPTION" | "MEMBERSHIP" | "FREE_TRIAL" | "SOFTWARE_LICENSE" | "DOMAIN_RENEWAL" | "SERVICE_PLAN" | "RECURRING_BILL" | "OTHER_RECURRING";

export interface CancellationTarget {
  obligation_id: string;
  workspace_id: string;
  user_id: string;
  connection_id: string | null;
  external_resource_id: string | null;
  merchant_id: string;
  merchant_domain: string | null;
  name: string;
  plan_name: string | null;
  billing_amount: string | null;
  billing_currency: string | null;
  billing_interval: SubscriptionInterval | null;
  billing_interval_count: number;
  next_renewal_at: string | null;
  auto_renew: boolean | null;
  revision: string;
}

export interface CancellationPreview {
  target: CancellationTarget;
  method: string;
  provider_revision: string;
  provider_state: string;
  economics?: Pick<CancellationTarget, "plan_name" | "billing_amount" | "billing_currency" | "billing_interval" | "billing_interval_count" | "next_renewal_at"> | null;
  payload: Record<string, unknown>;
  expected_effect: string;
  access_ends_at: string | null;
  fee: string | null;
  refund: string | null;
  management_url: string | null;
  warnings: string[];
  inspected_at: string;
  expires_at: string;
}

export interface CancellationAttempt {
  id: string;
  obligation_id: string;
  status: string;
  method: string;
  verification_status: string;
  payload_hash: string;
  preview: CancellationPreview;
  expires_at: string | null;
  failure_code: string | null;
}

export interface RecurringSubscription {
  id: string;
  merchant_id: string;
  name: string;
  obligation_type: SubscriptionType;
  plan_name: string | null;
  status: SubscriptionStatus;
  confidence: string;
  billing_amount: string | null;
  billing_currency: string | null;
  billing_interval: SubscriptionInterval;
  interval_count: number;
  monthly_equivalent: string | null;
  yearly_equivalent: string | null;
  next_renewal_at: string | null;
  trial_ends_at: string | null;
  trial_conversion_amount: string | null;
  trial_conversion_interval: SubscriptionInterval | null;
  trial_conversion_interval_count: number;
  auto_renew: boolean | null;
  started_at: string | null;
  last_verified_at: string | null;
  revision: number;
  attention_reasons: string[];
  verification_status: string | null;
  review_state: string;
  review_after: string | null;
  cancellation_connection_id: string | null;
  cancellation_external_resource_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface SubscriptionSummary {
  currency_totals: {currency: string; monthly_equivalent: string; yearly_equivalent: string; obligation_count: number}[];
  known_count: number;
  unknown_cost_count: number;
  coverage: "INCOMPLETE";
  label: string;
}

export interface PreventedRenewals {
  label: string;
  count: number;
  unknown_amount_count: number;
  currency_totals: { currency: string; renewal_amount: string; renewals: number }[];
  explanation: string;
}

export interface SubscriptionEvidence {
  id: string;
  obligation_id: string;
  evidence_type: string;
  source_type: string;
  connection_id: string | null;
  external_resource_id: string | null;
  merchant_text: string | null;
  plan_text: string | null;
  amount: string | null;
  currency: string | null;
  billing_interval: SubscriptionInterval | null;
  interval_count: number;
  effective_at: string | null;
  renewal_at: string | null;
  confidence: string;
  observed_at: string;
}

export interface SubscriptionPrice {
  id: string;
  amount: string;
  currency: string;
  billing_interval: SubscriptionInterval;
  interval_count: number;
  effective_from: string;
  effective_until: string | null;
  evidence_id: string;
}

export interface SubscriptionInput {
  name: string;
  obligation_type: SubscriptionType;
  plan_name: string | null;
  billing_amount: string | null;
  billing_currency: string | null;
  billing_interval: SubscriptionInterval;
  interval_count: number;
  next_renewal_at: string | null;
  trial_ends_at: string | null;
  trial_conversion_amount: string | null;
  trial_conversion_interval: SubscriptionInterval | null;
  trial_conversion_interval_count: number;
  auto_renew: boolean | null;
  status?: "CANDIDATE" | "TRIAL" | "ACTIVE" | "PAUSED" | "EXPIRED" | "UNKNOWN";
  request_id?: string;
}

export interface SubscriptionCancellationProfile {
  id: string;
  name: string;
  merchant_domain: string;
  configuration_id: string;
  connection_ids: string[];
  granted_connection_ids: string[];
}
