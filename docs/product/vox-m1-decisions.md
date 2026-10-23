# Vox milestone 1: accepted decisions and delivery contract

Source: Michael's accepted checklist and decisions, 2026-10-23. The four current PDFs and supplied design files remain authoritative. This document records decisions; it does not claim acceptance.

## Build order

1. Real web Vox call: back-and-forth, interruption, authoritative classes/schedule, two-minute standby then local Hey NavoX, network recovery with context, clean hangup, per-call costs.
2. Shared shell and Today.
3. Google/Microsoft/Apple calendar connection and scheduling.
4. Memory and privacy.
5. Classes, Planner, Inbox, Connections, Settings.
6. Noa.
7. Board.

Native locked-phone audio is a later separate part. The web app must remain open. A test deployment follows each milestone. Mobile emulation is engineering evidence only; Michael tests/records the physical phone. Physical-phone acceptance is pending until that evidence exists.

## Voice and configuration

LiveKit Cloud carries the call. OpenAI realtime speech is primary, with an automatic STT -> language model -> TTS fallback. Provider, endpoint, model and voice selections must be separate configuration values; a future supported self-hosted adapter must not require rebuilding Vox UI. Provider errors and partial output must not replay a spoken answer or duplicate a tool action.

Device VAD gates microphone audio before upload. Quiet/standby audio is not sent continuously to a provider. Standby occurs after two quiet minutes; local wake or tap resumes the same conversation without a repeated greeting. Keep NavoX conversation identity independent of provider session identity. Rollover before provider caps, restore heard context and pending tool state, test audio after restoration. Measure seamless behavior rather than assuming it.

Track server-owned speaking minutes, idle/connected minutes, modality tokens, provider/model, estimated costs, pricing version, and incomplete/reconciled status. Unknown prices/usage are unknown, never zero. Supply simple daily totals per student. Raw call audio and secrets do not belong in logs.

Starting per-student budgets: 60 voice minutes/day, 600/month; 300000 tokens/day, 3000000/month; gentle notice at 80%. Budgets are persistent runtime config editable without deployment. Student-local day/month boundaries apply.

## Resolved UI/Noa decisions

Follow UI brief for global Vox placement/copy. Preserve Noa prototype typography/effects inside Vox; use shared system elsewhere. Visible small controls retain appearance with hit areas >=44px; disclose necessary spacing changes. Include Apple/iCloud in Connections.

Use PDF animation timings; mouth moves from actual playback only. Focus credit requires >=15 real elapsed minutes, including an early stop after that point. Paused streak days preserve streak and spend no freeze. Build day3/day7/day14 rewards; days21/30/50/100 and seasonal rewards stay locked Coming soon pending designs. Reward definitions and unlock availability are data-driven.

Course colors: #4FD1B5 #7AA2F7 #B69CFF #F5B14C #FF8A9B #5BD38C #FF8A5B #67D4F0.

## Memory and approvals

Auto-expiry deletes transcript plus its summaries, retaining independently visible/editable saved facts. Explicit Forget removes source and everything derived from it. Off-record mode saves no conversation or learned memory.

Read back every affected change, including displaced study work and alarms, before execution. Keep on-screen approval for long email drafts. Approval binds exact action; changes require new approval.

## Board decisions

Fill fewer than four eligible suggestion cards with starter cards. Calibrate weak/close semantic-search thresholds with tests and disclose chosen values. Add DELETE /documents/{id} with complete cleanup. Unverifiable answers show grey Not verified. Use only the new16-action contract.

## Acceptance and evidence

A milestone requires: test link; acceptance checklist marked pass/not done; deviation list. Real phone, student phrase review and longitudinal usage targets cannot be passed by mocks, text tests or emulation. Student/usage metrics are Waiting on real usage until Michael recruits participants and evidence exists. Preserve existing working behavior unless the approved spec changes it.

Current milestone status: IN PROGRESS. No live-provider or physical-phone acceptance has been established by this branch.
