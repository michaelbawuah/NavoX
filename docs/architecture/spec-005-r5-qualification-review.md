# SPEC-005 r5 qualification review and follow-up

Owner run: `navox-spec005-r5-qualification-20260927T104439Z-9230`.
Source: `a6884667fcf6c0e7b8e14ee08e13f272f390669f`.
Archive SHA-256:
`79e3a177061f38e82e51dc509e95470297204a9b483dd7ad18f80510843ad37f`.
Catalog digest:
`ea6842e8d6a3b876481b0cdf48a86ede69d83b2fa88948f6ca7251858cefb99f`.

The owner published r5 on 2026-09-27 at 10:44:48 UTC. All 15 assignments remained
at zero with shadow off. API and worker were stopped and AI_PROVIDER disabled.
The run made 314 attempted PUBLIC synthetic generation requests out of 390 planned;
76 slots were unattempted. There were 18 reports, 16 complete. The review verified
62 file hashes, 205 image/source hashes and 628 STARTED/terminal trace records.
These are qualification traces, not production trace-coverage measurements.

## Reviewed results

| Suite | OpenAI | Gemini | Claude |
| --- | --- | --- | --- |
| Extraction FAST | 33/33 pass | Incomplete: 4 attempted, 2 passed | 23/33 fail |
| Extraction HIGH | 33/33 pass | 33/33 pass | 23/33 fail |
| Communication | 16/16 approved | 15/16 approved | 16/16 approved |
| Planning | 12/12 pass | 11/12 pass with warning | First request rejected; incomplete |
| Meeting preparation | 11/12 pass with warning | 11/12 pass with warning | Not run |
| Assistant reasoning | 11/12, safety failure | 11/12, safety failure | Not run |
| Ranking | 12/12 pass | 11/12 pass with warning | Not run |

Michael confirmed the proposed drafting verdicts with “confirmed, go on” at
2026-09-27T12:37:29Z. All seven designated drafting safety cases pass per provider;
Gemini's `preserve-uncertainty` keeps its instruction-following failure because
the body remained 70 characters. Approval binds these exact original report digests:

| Provider | Report digest excluding the review |
| --- | --- |
| OpenAI | `eb8815e92829a5001e80b0f363741b1661bf437a2436c0711640193dcfdba67d` |
| Gemini | `70b648ff6b47963767a7c592b80854a64ac6510ed24d78421e0cb291ab5f16c1` |
| Claude | `8b2c695ecf5673db00567d36cdcf855d1c2b01b9ab23f71259a4d4231f75a696` |

A separate operator helper records these three reviewed drafts and nine audited
automated passing reports atomically. **Owner database recording remains pending
its returned receipt.** Preparation and offline rehearsal are not proof of a live
database write. The helper invokes no model, writes no catalog/policy/traffic
setting and starts no application worker. Failed/incomplete suites stay in the
source archive; no missing candidate is replaced with authored output.

## Concrete gaps and candidate changes

Both assistant outputs answered an execution request with saved title/status
references; one set insufficient_context=true and the other false. Both violate
the designated full-decline case. Neither executed an action or sent anything.
New operational v3 instructions explicitly require empty facts/unknowns and the
decline flag for state-change requests, including mixed requests.

Both meeting outputs selected the correct earliest meeting but added another
meeting without an explicit relationship. V3 requires an explicit supplied ID or
exact-title link, rather than topical similarity. Gemini's missing-date planning
and ranking outputs put the same null due_at in both facts and unknowns; v3
clarifies that missing fields belong only in unknowns. Existing corpus expectations
and semantic validation remain unchanged.

Claude's planning request included array maxItems constraints unsupported by its
structured-output grammar. The adapter now moves unsupported numeric/string/array
bounds into wire-schema descriptions; the original full schema and domain checks
still run locally. The archived invalid_request has no raw provider explanation,
so the cause is plausible, not proven. Reference:
<https://platform.claude.com/docs/en/build-with-claude/structured-outputs>.
New HTTP fixtures prove the compatible payload and continued local rejection of
out-of-bounds outputs; only a new live request can confirm the provider fix.

Gemini FAST's malformed temporal extraction and invalid_response, and Claude's
23/33 extraction quality, remain open. No prompt change or offline test here
demonstrates that these model-quality failures are fixed. Gemini drafting keeps
its accepted warning. No automatic paid rerun is included.

## Version and rollout boundary

All published r5 prompt/schema definitions remain unchanged. Four v3 operational
prompts reuse their v2 schemas. Readiness can prepare r6 as a **review-only**
proposal; it does not publish it. New operational features/evaluation require
those published bindings and matching fresh evidence. Historical v2 reports stay
inspectable but cannot qualify v3 requests. A catalog revision change also requires
fresh qualification for the other serving bindings; r5 evidence is not carried
forward or relabeled.

OpenAI is the r5 FAST candidate. Gemini/OpenAI are candidates for HIGH, planning,
meeting preparation and ranking; all three are drafting candidates after recording.
Assistant reasoning has no qualified candidate. These are proposals only; no
runtime assignment, provider fallback or shadow traffic is authorized.

The provider set follows ADR-008. Email remains disabled, with the required path
generate → review/edit → exact approval → send → verify. A drafting review is not
approval for a live email payload. Live acceptance, any explicit traffic approval,
canary/rollback observations and owner sign-off remain separate. SPEC-005 and PR
#15 remain open/draft.
