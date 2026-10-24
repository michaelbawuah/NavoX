# Vox voice-credit batching

Voice allowance is reserved before audio is admitted. Each server grant holds at
most 1000 milliseconds of voice capacity and expires within one second. The grant
is clipped at the current local day, local month, media lease and call deadlines.

A grant binds its user, workspace, call, transport stream, exact media connection,
generation, worker nonce and transport sequence/cumulative checkpoint. Issuance
retains the User lock and a shared policy advisory lock; operator policy updates
take the matching exclusive advisory lock. Other workers see outstanding credits
when calculating remaining allowance.

The worker consumes only its own committed credit. Zero globally unallocated
voice capacity does not invalidate that already-issued credit. Provider admission
can present the exact holder proof for this case; provider token reservations
remain synchronous and must commit before paid I/O.

## Frames and persistence

Every audio frame still receives fresh database media authority. Batching does
not cache logout, reset, pause, ownership, session or media-expiry authorization.

Input and output have separate encoded-audio timelines. Same-direction burst
frames queue in order; the two directions share a union so simultaneous activity
is counted once. This is a server VAD/encoded-activity estimate, not a provider
invoice, proof that a person spoke, or proof that audio was heard. Pending windows
are bounded by the current one-second grant.

Frames consume local credit without metering writes or full budget aggregates.
Accounting flushes at the credit boundary, heartbeat, provider reservation and
shutdown. A flush waits for already-admitted windows to elapse before settling
observed activity. It does not extrapolate a reserved second into actual speech.
Settlement uses a stable grant event and digest; changed replays or a changed
transport checkpoint fail closed.

## Policy, periods and crashes

Lowered operator budgets apply to new grants. A previously issued grant remains
usable until its deadline, for at most one second. Immediate authority changes
still stop the next frame through its fresh media proof.

Voice settlement is attributed to the grant's local period. Grant deadlines
prevent an old-period credit from authorizing new-period activity. Idle and
connected time remain separate from voice allowance; transport providers may
charge for both speaking and idle room time.

An expired unsettled grant is never refunded automatically. It remains
`reserved_voice_ms` and becomes `uncertain_voice_ms`, reducing available quota
without increasing `speaking_ms`. Only the worker's observed accounting can
settle and refund its unused capacity. A failed or uncertain commit retains the
exact retry and durable hold. Old workers cannot settle a successor's credit.

The performance guarantee is bounded accounting operation count per credit pulse
and explicit reservation/shutdown boundaries. No latency or throughput benchmark
is claimed.
