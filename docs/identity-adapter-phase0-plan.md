# Identity adapter Phase 0, implementation plan

Written 2026-07-27 and verified against the repo. Tracked as issue #78 under #80,
which carries the same content. Scope is a scripted identity-breach event family
running through the real pipeline: no live feeds, no DB migration. Ends 2026-07-31.

Get a scripted identity-breach event family flowing through the real pipeline end to end (ingest, triage, case, playbook match, timeline), with templates shaped like real HIBP and Enzoic responses. No live feed integrations, no DB migration. The full plan is below, so this issue stands on its own.

One engineer, five days, ending 2026-07-31. No expansion into pilot hardening.

Background: a review arc on 2026-07-27 concluded that identity findings belong inside soctalk as another alert source for existing operators rather than as a separate product. The consumer and standalone framings were dropped, and the browser-extension actuator was deferred. Detection here is commodity since the feeds are rented. What soctalk adds is triage with authorization and expectedness context, case tracking, and a close with graded evidence. This is one of two candidate directions under evaluation (see the parent issue). The signal it should produce is whether operators want triaged identity findings in their case queue enough to run it against their own domains.

### Day by day

- [ ] Day 1, core source projection: `claim_run()` carries `a.source` plus `full_log_redacted`, `decoder` and `template_*`; `_build_state()` gates Wazuh-only behavior; routing unit tests
- [ ] Day 2, identity authz branch: `identity_account_target()`, user-entity branch in `authorization_context_for_alert()`, render as `target_account=`, store integration tests
- [ ] Day 3, identity worker and timeline: no-network `identity_worker_node`, graph route, identity `TIMELINE_ENTRY`, reducer and claim tests
- [ ] Day 4, demo-seed family: `identity_events()` fixtures, CLI knobs, `to_adapter_event()` source support, README update
- [ ] Day 5, response playbooks and end to end: three identity playbook YAMLs, dispatch tests, seed verification

If day 3 ends behind schedule, drop the broker-exposure texture and the extra fixture variety. Do not drop source propagation, Wazuh gating, the user-entity authz branch, or the response dispatch proof.

### Prerequisites (verify before day 1)
- **Migrations applied**, especially `v1_0018` (source events + checkpoints) and the authored-response-playbook migration if using API authoring.
- **Playbook load path, pick one explicitly:** file-loaded playbooks must sit in `SOCTALK_RESPONSE_PLAYBOOK_DIR` as `*.yaml`/`*.yml` (there `status: active` is honored); API-authored playbooks ignore the definition's status field, default to shadow on save, and require `POST .../activate`.
- **Response execution enabled:** `SOCTALK_RESPONSE_EXECUTOR` not disabled, `SOCTALK_RESPONSE_DISPATCH_KILL` unset/false.
- **Capability config in tenant policy:** `response_webhook_url` for `notify_webhook`; `response_action_endpoints.<id>.url` (plus optional secret) for `external_action`. Note that `endpoint: iam` is an operator-configured endpoint id, not a URL or built-in connector, so the playbook is schema-valid but inert without it.
- **Tokens:** adapter + worker tokens minted with the shared `SOCTALK_ADAPTER_SIGNING_KEY` / `ADAPTER_SIGNING_KEY_PATH`; worker runtime needs `SOCTALK_API_URL` and `WORKER_TOKEN_PATH` (plus the stub-LLM env for the cost-safe demo-seed flow).

### Follow-up (not day-1 scope)
The frontend pipeline map has no `identity` worker node, if `worker_started("identity")` must render in the replay panel, that's a small frontend addition to schedule after the backend lands.

---

# Implementation plan (verified against the repo, 2026-07-27)

**Phase 0 Plan**

The HIBP and Enzoic shapes below come from their stable public API responses.

**What was verified in the code**

1. `AdapterEvent` supports non-Wazuh sources: `source` is a plain `str` defaulting to `"wazuh"`, max 32, and `severity` is `0..15` in [events.py](https://github.com/soctalk/soctalk/blob/main/src/soctalk_wire/events.py#L56). `assess()` bands are exactly `>=8 real`, `5..7 unclear`, `3..4 likely_fp`, `<3 high_conf_fp`, with MITRE only lifting the `<3 high_conf_fp` path to `unclear` (it does not veto mid-band results) in [triage.py](https://github.com/soctalk/soctalk/blob/main/src/soctalk/core/ir/triage.py#L257).

2. `POST /api/internal/adapter/events` is the real ingress. It builds evidence and calls `triage_event()` in [adapter.py](https://github.com/soctalk/soctalk/blob/main/src/soctalk/core/api/adapter.py#L96). Checkpoints are per `(tenant_id, source)` in handler SQL [adapter.py](https://github.com/soctalk/soctalk/blob/main/src/soctalk/core/api/adapter.py#L189) and migration [v1_0018](https://github.com/soctalk/soctalk/blob/main/alembic/versions/v1_0018_source_events_and_checkpoints.py#L120).

3. Extend `scripts/demo-seed`: producer is `build_plan()` plus `to_adapter_event()` in [seed.py](https://github.com/soctalk/soctalk/blob/main/scripts/demo-seed/seed.py#L55), using `EventTemplate` families from [families.py](https://github.com/soctalk/soctalk/blob/main/scripts/demo-seed/families.py#L42). `to_adapter_event()` currently hardcodes `source: "wazuh"` [seed.py](https://github.com/soctalk/soctalk/blob/main/scripts/demo-seed/seed.py#L178).

4. Response playbooks match `rule_groups` and `rule_ids` in [registry.py](https://github.com/soctalk/soctalk/blob/main/src/soctalk/response/registry.py#L102). Criteria are OR’d, so `rule_groups: [identity]` is family-wide; subtype playbooks should use `rule_ids: [identity:...]`.

5. `authorization_context_for_alert()` currently requires a host entity and returns `None` otherwise [authz_shadow.py](https://github.com/soctalk/soctalk/blob/main/src/soctalk/core/ir/authz_shadow.py#L382). `AuthorizationActivity` account track requires `host/account/action` [authorization.py](https://github.com/soctalk/soctalk/blob/main/src/soctalk/models/authorization.py#L327).

6. `INVESTIGATE` routes to `wazuh_worker` [builder.py](https://github.com/soctalk/soctalk/blob/main/src/soctalk/graph/builder.py#L79). `_build_state()` projects every alert as Wazuh-shaped, including agent fields, hostname regex, hardcoded pending observable source, and level >=13 fake TI enrichment [main.py](https://github.com/soctalk/soctalk/blob/main/src/soctalk/runs_worker/main.py#L210).

**Changes by file**

`src/soctalk/core/api/worker_runs.py`
- In `claim_run()`, include `a.source` in each `alert_payloads` item.
- Extend the LATERAL select to carry `se.full_log_redacted`, `se.decoder`, `se.template_hash`, `se.template_version`; no migration needed because columns already exist in [v1_0018](https://github.com/soctalk/soctalk/blob/main/alembic/versions/v1_0018_source_events_and_checkpoints.py#L68).
- Pass `source=alert[0]["source"]` already happens; identity branch will depend on it.

`src/soctalk/runs_worker/main.py`
- In `_build_state()`, introduce `_adapter_source(alert)` and `_target_display(alert)`.
- Gate Wazuh-only behavior:
  - hostname regex only when `source == "wazuh"`;
  - agent projection only for Wazuh;
  - `pending_observables` source becomes the adapter source, not hardcoded `"wazuh"`;
  - level >=13 demo TI block runs only for Wazuh.
- Keep numeric severity mapping but rename `_wazuh_level_to_severity()` to `_level_to_severity_label()` or wrap it to avoid Wazuh semantics leaking into identity.

`src/soctalk/graph/builder.py`
- Add `identity_worker` node.
- Route `INVESTIGATE` to `identity_worker` when any alert has `adapter_source == "identity"` or `rule_groups` contains `identity`; otherwise keep Wazuh.
- **`adapter_source` does not exist yet. Create the full chain:** `claim_run()` must include `source` in each alert payload (it selects `a.source` today and drops it) → `_build_state()` copies it onto each supervisor alert as `adapter_source` → the router reads that field. Commit 1 is a hard prerequisite for this routing.
- Add unit coverage next to [test_llm_plumbing_unit.py](https://github.com/soctalk/soctalk/blob/main/tests/v1/test_llm_plumbing_unit.py#L228).

`src/soctalk/workers/identity.py` new
- `identity_worker_node(state)` summarizes identity evidence that was already ingested. It makes no network calls.
- Emits `worker_started("identity")` / `worker_result("identity")`.
- Adds deterministic findings from `identity:*` rule IDs and persisted `full_log_redacted`/entities/IOCs.

`src/soctalk/core/ir/authz_shadow.py`
- Add identity extraction branch in `authorization_context_for_alert()`:
  - existing host branch unchanged;
  - if `source == "identity"` and no host, use first `user` entity as account;
  - target becomes `identity_account_target(user)`, e.g. `account:alice@example.com`;
  - build `AuthorizationActivity(track=ACCOUNT, host=target, account=user, action=rule_id or "unknown", time=ts)`.
- Preserve Wazuh user-only behavior returning `None`.

`src/soctalk/models/authorization.py`
- Add small helper `identity_account_target(account: str) -> str`.
- Update account-track docstring to document Phase 0 identity pseudo-targets without changing DB shape.

`src/soctalk/authorization/render.py`
- In `_activity_line()`, render `host` values with the identity prefix as `target_account=...`, not `host=...`.

`src/soctalk/core/ir/triage.py`
- Add identity `TIMELINE_ENTRY` append after `promote_alert_to_case()` succeeds and for attached/correlated identity events.
- Include only concise normalized fields in timeline summary. Do not put provider raw JSON there.

`scripts/demo-seed/families.py`
- Add optional `source`, `decoder`, `full_log`, `raw` fields to `EventTemplate`.
- Add `identity_events(rng, breach, stealer, password_reuse, broker)` with provider-shaped fixtures:
  - HIBP breach objects: `Name`, `Title`, `Domain`, `BreachDate`, `AddedDate`, `PwnCount`, `DataClasses`, `IsVerified`, `IsSensitive`, `IsSpamList`, `IsMalware`.
  - HIBP Pwned Passwords range result: SHA-1 prefix plus suffix/count only; never plaintext.
  - Enzoic exposure/credential objects: exposure id/title/date/category/entries/passwordType plus username/domain/exposure ids.
- Send durable details through `description`, `full_log`, entities, IOCs, rule IDs, and rule groups. Do not rely on `AdapterEvent.raw`; it is currently ignored by `adapter.py` evidence building [adapter.py](https://github.com/soctalk/soctalk/blob/main/src/soctalk/core/api/adapter.py#L139).

`scripts/demo-seed/seed.py`
- Import `identity_events()`.
- Add CLI counts: `--identity-breach`, `--identity-stealer`, `--identity-password-reuse`, `--identity-broker`.
- `to_adapter_event()` uses `ev.source`, `ev.decoder`, `ev.full_log`, `ev.raw`.
- `facts_for_plan()` adds identity account-track facts for selected broker/known-exposure demos using `identity_account_target()`.

`examples/response-playbooks/identity-*.yaml` new
- Add sample YAMLs below. NOTE: files are only loaded from `SOCTALK_RESPONSE_PLAYBOOK_DIR`; point that at the examples dir for the demo, or author via the API and activate (API rows default to shadow and ignore the file's `status`).
- Use `params.body`. `_annotate_investigation()` reads `body`, and capability param allowlists now reject unknown keys at authoring time.

**Severity mapping**

| Event type | Rule ID | Groups | Severity | Triage band | Source grounding |
|---|---|---|---:|---|---|
| `breach_hit` | `identity:breach_hit` | `identity,hibp,breach` | 8 | `real` | HIBP verified breach, sensitive `DataClasses` |
| `stealer_log_hit` | `identity:stealer_log_hit` | `identity,enzoic,stealer` | 13 | `real` | Enzoic credential exposure / stealer-style password type |
| `password_reuse` | `identity:password_reuse` | `identity,hibp,password` | 10 | `real` | HIBP k-anon password hash suffix count |
| `broker_exposure` | `identity:broker_exposure` | `identity,broker,exposure` | 6 | `unclear` | public/broker PII, no password/stealer signal |

**Example playbooks**

All three validate against `parse_response_playbook_text()` (schema confirmed: `ResponsePlaybook{id, version, tenant, status, priority, applies_to, response}`, `extra="forbid"`; `on_close` accepts only `annotate_investigation`; `when` supports `var`/comparisons/`and,or,!,!!`/`in` over `disposition, worker_disposition, floor_vetoed, verdict_confidence, severity, rule.groups, rule.ids, mitre.techniques, mitre.tactics`). The `external_action` examples execute only if `response_action_endpoints.iam` is configured in tenant policy, see Prerequisites.

```yaml
id: identity-exposure-family-notify
version: 1
status: active
priority: 80
applies_to:
  rule_groups: [identity]
response:
  on_escalate:
    - capability: annotate_investigation
      params: {body: "Identity exposure escalated. Verify account owner, exposure class, and rotation status."}
    - capability: notify_webhook
      when: {">=": [{"var": "severity"}, 8]}
  on_close:
    - capability: annotate_investigation
      params: {body: "Identity exposure closed with documented rationale."}
```

```yaml
id: identity-stealer-disable-account
version: 1
status: active
priority: 40
applies_to:
  rule_ids: [identity:stealer_log_hit]
response:
  on_escalate:
    - capability: annotate_investigation
      params: {body: "Stealer-log credential hit. Route for account containment approval."}
    - capability: external_action
      params: {endpoint: iam, action: disable_account}
      when: {">=": [{"var": "severity"}, 13]}
```

```yaml
id: identity-password-reuse-reset
version: 1
status: active
priority: 60
applies_to:
  rule_ids: [identity:password_reuse]
response:
  on_escalate:
    - capability: external_action
      params: {endpoint: iam, action: force_password_reset}
      when: {">=": [{"var": "severity"}, 9]}
    - capability: notify_webhook
```

**Tests**

Follow existing conventions: unit tests under `tests/v1`, integration tests marked `pytest.mark.integration` and skipped by `SKIP_INTEGRATION=1` per [pyproject.toml](https://github.com/soctalk/soctalk/blob/main/pyproject.toml#L81) and [conftest.py](https://github.com/soctalk/soctalk/blob/main/tests/v1/conftest.py#L9).

Add:
- `tests/v1/test_identity_phase0_unit.py`: wire validation for source `identity`; seed `identity_events()` output; `_build_state()` does not fake APT29/MISP for severity 13 identity; route `INVESTIGATE` to identity worker.
- `tests/v1/test_identity_phase0_integration.py`: call `triage_event()` or adapter handler with identity event, assert promoted case, `alert_source_events.rule_groups` contains `identity`, `claim_run()` returns source/full_log, authz context builds from user-only identity event.
- Extend `tests/v1/test_response_playbook_unit.py`: identity group broad match and subtype `rule_ids` match.
- Extend `tests/v1/test_response_dispatch_integration.py`: complete an identity run, assert response outbox row and envelope `rule.groups` includes `identity`.
- Extend reducer/timeline test: identity promotion appends `timeline_entry` and `consume_new_events()` projects it.

Commands:
`SKIP_INTEGRATION=1 uv run pytest tests/v1/test_identity_phase0_unit.py tests/v1/test_response_playbook_unit.py`
`uv run pytest tests/v1/test_identity_phase0_integration.py tests/v1/test_response_dispatch_integration.py -m integration`

**Commit sequence**

1. Core source projection: `claim_run()` source/full_log fields, `_build_state()` Wazuh gating, routing unit tests.
2. Identity authz branch: `identity_account_target()`, authz extraction/rendering, store integration tests.
3. Identity worker and timeline: no-network worker, graph route, identity timeline entries, reducer/claim tests.
4. Demo seed family: identity fixtures, CLI knobs, `to_adapter_event()` source support, README update.
5. Response playbooks and E2E: YAML examples, dispatch tests, dry-run/seed verification notes.

Scope cut line: if behind by end of day 3, cut broker-exposure close/authorization demo texture and extra fixture variety; do not cut source propagation, Wazuh bypass, user-entity authz branch, or response dispatch proof.

**Risks and contradictions found in the repo**

- `AdapterEvent.raw` exists but is not persisted by `adapter.py`; Phase 0 must encode provider facts into durable fields.
- `claim_run()` selects `a.source` but drops it today; identity routing cannot work until fixed.
- Current `_build_state()` can turn a severity 13 identity hit into fake Wazuh/MISP/APT29 evidence.
- Response matcher ORs criteria; `rule_groups: [identity]` is intentionally broad.
- Customer case facts currently return all `timeline_summary` despite doc text about filtering; keep identity timeline summaries non-sensitive.
- Fixed while reviewing this plan: the shipped example JSON playbooks passed `params.note` where the executor reads `params.body`, so the authored text was dropped. Examples corrected, param allowlists added, regression test in `tests/v1/test_response_examples_unit.py`.
