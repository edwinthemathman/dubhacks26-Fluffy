"""Plan submission, authorization, questions, and UI events.

The harness decides whether a proposal may run. It does not apply device effects
itself; the executor does that after reading the authorization record.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import httpx

from fluffy.authz.store import AuthorizationStore, action_hash
from fluffy.brain.safety import IncidentManager, SafetyCommand
from fluffy.clock import Clock
from fluffy.config import RuntimeSettings
from fluffy.evidence.evaluator import ContextView, evaluate_action
from fluffy.ids import new_id
from fluffy.models.contracts import (
    SCHEMA_VERSION,
    ActionProposal,
    AuthorizationRecord,
    CheckOutcome,
    DeveloperRequest,
    IncidentView,
    Observation,
    OutputMessage,
    Plan,
    PlanAction,
    PlanProposed,
    PlanResult,
    PlanSubmission,
    PossibleFallEvent,
    UiEvent,
)
from fluffy.tools.registry import Registry, ToolError, build_registry, canonical_args


@dataclass
class PendingQuestion:
    question_id: str
    session_id: str
    text: str
    choices: list[str]
    expires_at: Any
    actions: list[dict]
    trigger: dict
    intent: str
    reasoning_summary: str
    status: str = "open"


@dataclass
class Runtime:
    settings: RuntimeSettings
    clock: Clock
    registry: Registry
    authorizations: AuthorizationStore = field(default_factory=AuthorizationStore)
    observations: dict[str, Observation] = field(default_factory=dict)
    questions: dict[str, PendingQuestion] = field(default_factory=dict)
    safety: IncidentManager | None = None
    sequence: int = 0
    ui_clients: set = field(default_factory=set)
    executor: Any = None
    executor_online: bool = False
    queued: list[dict] = field(default_factory=list)
    results: dict[str, dict] = field(default_factory=dict)
    idempotent: dict[str, dict] = field(default_factory=dict)
    manual_until: dict[str, Any] = field(default_factory=dict)
    cooldown_until: Any = None
    playing_sound: dict[str, str] = field(default_factory=dict)
    speaker_client_id: str | None = None
    listening: str = "paused"
    stopped: bool = False
    integrations: list[dict] = field(default_factory=list)
    http: httpx.AsyncClient | None = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def __post_init__(self) -> None:
        if self.safety is None:
            self.safety = IncidentManager(self.settings.home)

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.settings.internal_token}"}

    async def emit(self, session_id: str, event_type: str, payload: dict) -> UiEvent:
        async with self._lock:
            self.sequence += 1
            event = UiEvent(
                event_id=new_id("evt"),
                session_id=session_id,
                sequence=self.sequence,
                timestamp=self.clock.now(),
                type=event_type,
                payload=payload,
            )
        encoded = json.loads(event.model_dump_json())
        dead = []
        for client in list(self.ui_clients):
            try:
                await client.send_json(encoded)
            except Exception:
                dead.append(client)
        for client in dead:
            self.ui_clients.discard(client)
        return event

    def remember_observation(self, observation: Observation) -> None:
        self.observations[observation.observation_id] = observation

    def enabled_tools(self) -> dict[str, bool]:
        by_provider = {item.get("provider"): item.get("readiness") for item in self.integrations}
        speech = by_provider.get("elevenlabs") in {"configured", "ready"}
        photon = by_provider.get("photon") in {"configured", "ready"}
        spotify = by_provider.get("spotify") == "ready"
        enabled = {}
        for name in self.registry.names():
            spec = self.registry.get(name)
            if spec.requires_provider == "elevenlabs":
                enabled[name] = speech
            elif spec.requires_provider == "photon":
                enabled[name] = photon
            elif spec.requires_provider == "spotify":
                enabled[name] = spotify
            else:
                enabled[name] = True
        return enabled

    async def refresh_integrations(self) -> list[dict]:
        if self.http is None:
            return self.integrations
        try:
            response = await self.http.get(f"{self.settings.home_url}/api/health")
            response.raise_for_status()
            self.integrations = response.json().get("integrations", [])
        except httpx.HTTPError:
            self.integrations = [
                {"provider": "spotify", "readiness": "unavailable", "detail": "output service is not reachable"},
                {"provider": "elevenlabs", "readiness": "unavailable", "detail": "output service is not reachable"},
                {"provider": "photon", "readiness": "unavailable", "detail": "output service is not reachable"},
            ]
        return self.integrations

    async def submit_developer(self, request: DeveloperRequest, idempotency_key: str | None) -> dict:
        if idempotency_key and idempotency_key in self.idempotent:
            return self.idempotent[idempotency_key]
        observation = Observation(
            observation_id=new_id("obs"),
            session_id=self.settings.session_id,
            kind="speech",
            captured_at=self.clock.now(),
            room=str(request.args.get("room") or self.settings.home.rooms[0].id),
            supports=["explicit_request"],
            targets=_targets(request.args),
            summary="Labeled developer explicit request. Not live perception.",
            source="developer",
        )
        self.remember_observation(observation)
        submission = PlanSubmission(
            schema_version=SCHEMA_VERSION,
            session_id=self.settings.session_id,
            intent=request.intent,
            trigger={"kind": "developer", "event": "explicit_request", "room": observation.room},
            actions=[
                ActionProposal(
                    action_id=new_id("act"),
                    tool=request.tool,
                    args=request.args,
                    evidence_ids=[observation.observation_id],
                )
            ],
            reasoning_summary="Developer explicit request.",
        )
        result = await self.submit(submission, explicit=True)
        if idempotency_key:
            self.idempotent[idempotency_key] = result
        if request.request_id:
            self.idempotent[request.request_id] = result
        return result

    async def submit(self, submission: PlanSubmission, explicit: bool, mode_override: str | None = None) -> dict:
        if submission.session_id != self.settings.session_id:
            return _error("invalid_arguments", "session does not match the active demo session")
        if _duplicate_caption(submission.actions):
            return _error("duplicate_caption", "announce already captions its text; do not also call show_caption")
        prepared: list[PlanAction] = []
        checks: list[CheckOutcome] = []
        minimum = 5
        ask = False
        for proposal in submission.actions:
            try:
                model = self.registry.validate_args(proposal.tool, proposal.args)
            except ToolError as exc:
                return _error(exc.error_code, exc.message)
            if not self.enabled_tools().get(proposal.tool, True):
                return _error("tool_unavailable", f"{proposal.tool} is disabled until its provider is ready")
            args = canonical_args(model)
            gate = self._hard_gate(proposal.tool, args)
            if gate is not None:
                return gate
            observations = []
            for evidence_id in proposal.evidence_ids:
                found = self.observations.get(evidence_id)
                if found is None or found.session_id != submission.session_id:
                    return _error("invalid_arguments", f"unknown evidence {evidence_id}")
                observations.append(found)
            if proposal.tool == "alert_caregiver":
                if not self._alert_allowed(proposal.args.get("incident_id"), explicit):
                    return _error("incident_not_eligible", "caregiver alerts require an eligible incident or an explicit help request")
            else:
                decision = evaluate_action(
                    self.settings.home,
                    proposal.tool,
                    args,
                    observations,
                    self._context(args),
                    submission.trigger.event,
                    explicit=explicit,
                )
                checks.extend(decision.checks)
                minimum = min(minimum, decision.score)
                if decision.outcome == "ask":
                    ask = True
                elif decision.outcome == "quiet":
                    return {
                        "status": "quiet",
                        "error_code": "insufficient_evidence",
                        "message": decision.detail,
                        "checks": [check.model_dump() for check in decision.checks],
                    }
            prepared.append(
                PlanAction(
                    action_id=proposal.action_id,
                    tool=proposal.tool,
                    args=args,
                    evidence_ids=list(proposal.evidence_ids),
                    action_class=self.registry.get(proposal.tool).action_class,
                )
            )
        if ask:
            return await self._ask(submission, prepared, checks)
        if mode_override:
            mode = mode_override
        elif any(action.tool == "alert_caregiver" for action in prepared):
            mode = "safety_workflow"
        elif explicit:
            mode = "explicit_request"
        else:
            mode = "automatic"
        if mode == "safety_workflow" and not checks:
            checks = [
                CheckOutcome(name=name, passed=True, detail="safety workflow")
                for name in (
                    "cue_supported",
                    "target_clear",
                    "evidence_fresh",
                    "context_agrees",
                    "support_sufficient",
                )
            ]
            minimum = 5
        return await self._authorize_and_send(submission, prepared, checks, minimum, mode)

    async def _ask(self, submission: PlanSubmission, actions: list[PlanAction], checks: list[CheckOutcome]) -> dict:
        question_id = new_id("qst")
        text = _question_text(actions[0])
        pending = PendingQuestion(
            question_id=question_id,
            session_id=submission.session_id,
            text=text,
            choices=["yes", "no"],
            expires_at=self.clock.now() + timedelta(seconds=30),
            actions=[action.model_dump(mode="json") for action in actions],
            trigger=submission.trigger.model_dump(mode="json"),
            intent=submission.intent,
            reasoning_summary=submission.reasoning_summary,
        )
        self.questions[question_id] = pending
        await self._caption(submission.session_id, text, "high", "question", question_id)
        await self.emit(
            submission.session_id,
            "question.opened",
            {"question_id": question_id, "text": text, "choices": ["yes", "no"], "checks": [c.model_dump() for c in checks]},
        )
        return {"status": "question", "question_id": question_id, "text": text, "checks": [c.model_dump() for c in checks]}

    async def resolve_question(self, question_id: str, answer: str, evidence_ids: list[str] | None, source: str) -> dict:
        pending = self.questions.get(question_id)
        if pending is None or pending.status != "open":
            return _error("invalid_arguments", "question is not open")
        if self.clock.now() > pending.expires_at:
            pending.status = "expired"
            return _error("plan_expired", "question expired")
        if source == "model":
            if not evidence_ids or not self._evidence_supports_answer(evidence_ids, answer, pending.session_id):
                return _error("insufficient_evidence", "the answer is not backed by a fresh reply")
        pending.status = "resolved"
        await self.emit(pending.session_id, "question.resolved", {"question_id": question_id, "answer": answer, "source": source})
        if answer != "yes":
            return {"status": "declined", "question_id": question_id}
        gate = self._recheck(pending.actions)
        if gate is not None:
            return gate
        confirmation = Observation(
            observation_id=new_id("obs"),
            session_id=pending.session_id,
            kind="speech",
            captured_at=self.clock.now(),
            room=pending.trigger.get("room"),
            supports=["explicit_request"],
            targets=[],
            summary=f"Confirmed {question_id}",
            source="developer" if source == "button" else "live",
        )
        self.remember_observation(confirmation)
        actions = []
        for raw in pending.actions:
            raw = dict(raw)
            raw["action_id"] = new_id("act")
            raw["evidence_ids"] = [confirmation.observation_id]
            actions.append(ActionProposal.model_validate(raw))
        submission = PlanSubmission(
            schema_version=SCHEMA_VERSION,
            session_id=pending.session_id,
            intent=pending.intent,
            trigger=pending.trigger,
            actions=actions,
            reasoning_summary=pending.reasoning_summary,
        )
        return await self.submit(submission, explicit=True, mode_override="confirmed")

    def _evidence_supports_answer(self, evidence_ids: list[str], answer: str, session_id: str) -> bool:
        for evidence_id in evidence_ids:
            found = self.observations.get(evidence_id)
            if found is None or found.session_id != session_id:
                return False
            if found.kind != "speech":
                return False
            if self.clock.now() - found.captured_at > timedelta(seconds=self.settings.home.room_cue_fresh_s):
                return False
            if answer.lower() not in found.summary.lower() and "spoken_request" not in found.supports:
                return False
        return True

    def _recheck(self, actions: list[dict]) -> dict | None:
        for raw in actions:
            gate = self._hard_gate(raw["tool"], raw["args"])
            if gate is not None:
                return gate
        return None

    def _hard_gate(self, tool: str, args: dict) -> dict | None:
        now = self.clock.now()
        if tool == "play_sound":
            if self.cooldown_until and now < self.cooldown_until:
                return _error("cooldown", "playback cooldown is active")
            room = args.get("room")
            if self.settings.home.no_restart_if_same_track and self.playing_sound.get(room) == args.get("sound"):
                return _error("already_playing", "that sound is already playing")
        return None

    def _context(self, args: dict) -> ContextView:
        now = self.clock.now()
        manual = {target for target, until in self.manual_until.items() if until > now}
        return ContextView(
            now=now,
            cancelled=self.stopped,
            manual_targets=manual,
            cooldown_until=self.cooldown_until,
            contradictory=False,
        )

    def _alert_allowed(self, incident_id: str | None, explicit: bool) -> bool:
        incident = self.safety.incident if self.safety else None
        if incident is None or incident.incident_id != incident_id:
            return False
        del explicit
        if incident.sent:
            return False
        return incident.phase == "alert_pending"

    async def _authorize_and_send(
        self,
        submission: PlanSubmission,
        actions: list[PlanAction],
        checks: list[CheckOutcome],
        score: int,
        mode: str,
    ) -> dict:
        now = self.clock.now()
        plan = Plan(
            plan_id=new_id("pln"),
            session_id=submission.session_id,
            created_at=now,
            expires_at=now + timedelta(seconds=self.settings.home.plan_ttl_s),
            trigger=submission.trigger,
            intent=submission.intent,
            actions=actions,
            authorization_id=new_id("auth"),
            reasoning_summary=submission.reasoning_summary,
        )
        record = AuthorizationRecord(
            authorization_id=plan.authorization_id,
            session_id=plan.session_id,
            plan_id=plan.plan_id,
            action_hash=action_hash(plan.session_id, actions),
            evidence_ids=sorted({evidence_id for action in actions for evidence_id in action.evidence_ids}),
            checks=checks,
            score=score,
            mode=mode,  # type: ignore[arg-type]
            action_classes={action.action_id: action.action_class for action in actions},
            expires_at=plan.expires_at,
            created_at=now,
        )
        self.authorizations.put(record)
        if any(action.tool == "play_sound" for action in actions):
            self.cooldown_until = now + timedelta(seconds=self.settings.home.clap_cooldown_s)
            for action in actions:
                if action.tool == "play_sound":
                    self.playing_sound[action.args["room"]] = action.args["sound"]
        proposed = PlanProposed(plan=plan)
        payload = json.loads(proposed.model_dump_json())
        await self.emit(plan.session_id, "plan.proposed", payload)
        await self._send_to_executor(payload)
        return {
            "status": "submitted",
            "plan_id": plan.plan_id,
            "authorization_id": plan.authorization_id,
            "schema_version": SCHEMA_VERSION,
            "executor_online": self.executor_online,
        }

    async def _send_to_executor(self, payload: dict) -> None:
        if self.executor is None:
            self.queued.append(payload)
            await self.emit(payload["plan"]["session_id"], "executor.status", {"online": False})
            return
        await self.executor.send_json(payload)

    async def executor_hello(self, socket) -> None:
        self.executor = socket
        self.executor_online = True
        await self.emit(self.settings.session_id, "executor.status", {"online": True})
        queued = self.queued
        self.queued = []
        now = self.clock.now()
        for payload in queued:
            from datetime import datetime

            expiry = datetime.fromisoformat(payload["plan"]["expires_at"])
            if expiry <= now:
                await self.emit(payload["plan"]["session_id"], "plan.decision", {"plan_id": payload["plan"]["plan_id"], "decision": "rejected", "reason": "plan_expired"})
                continue
            await socket.send_json(payload)

    async def handle_executor_message(self, message: dict) -> None:
        kind = message.get("type")
        session_id = message.get("session_id") or message.get("result", {}).get("session_id") or self.settings.session_id
        if kind == "plan.result":
            self.results[message["result"]["plan_id"]] = message["result"]
            await self.emit(session_id, kind, message)
            await self._complete(PlanResult.model_validate(message["result"]))
            return
        if kind in {"plan.decision", "action.started", "action.result"}:
            await self.emit(session_id, kind, message)
            if kind == "action.result":
                result = message.get("result", {})
                if result.get("tool") == "stop_sound" and result.get("status") == "succeeded":
                    room = None
                    new_state = result.get("new_state") or {}
                    if isinstance(new_state, dict):
                        room = new_state.get("room")
                    if room:
                        self.playing_sound.pop(room, None)
                if result.get("tool") == "play_sound" and result.get("status") != "succeeded":
                    self.playing_sound.clear()

    async def _complete(self, result: PlanResult) -> None:
        if result.action_results and all(item.tool in {"announce", "show_caption"} for item in result.action_results):
            return
        text = _completion_text(result)
        if not text:
            return
        await self._caption(result.session_id, text, "normal", "completion", new_id("msg"))

    async def _caption(self, session_id: str, text: str, priority: str, source: str, message_id: str) -> None:
        if self.http is None:
            return
        ttl = 10 if priority == "normal" else 30
        try:
            await self.http.post(
                f"{self.settings.home_url}/api/tools/show_caption",
                json={"text": text, "priority": priority, "ttl_s": ttl},
                headers={
                    **self.headers(),
                    "X-Plan-Id": "pln_harness",
                    "X-Action-Id": message_id,
                    "X-Session-Id": session_id,
                },
            )
        except httpx.HTTPError:
            return

    async def sync_incident(self) -> None:
        if self.http is None or self.safety is None or self.safety.incident is None:
            return
        incident = self.safety.incident
        view = None
        if incident.phase not in {"resolved_ok", "cancelled", "idle"}:
            view = IncidentView(
                incident_id=incident.incident_id,
                phase=incident.phase,
                room=incident.room,
                audio_failed=incident.audio_failed,
                question_id=incident.question_id,
                question_text=incident.question_text,
            )
        try:
            await self.http.post(
                f"{self.settings.home_url}/internal/incident",
                json=None if view is None else json.loads(view.model_dump_json()),
                headers=self.headers(),
            )
        except httpx.HTTPError:
            return
        await self.emit(
            incident.session_id,
            "incident.updated",
            view.model_dump(mode="json") if view else {"incident_id": incident.incident_id, "phase": incident.phase},
        )

    async def apply_safety(self, commands: list[SafetyCommand]) -> None:
        for command in commands:
            await self.sync_incident()
            if command.kind == "stop_sound":
                await self._safety_plan("stop_sound", {"room": self.safety.incident.room}, "Pause music for a check-in")
            elif command.kind == "announce":
                await self._speak_question(command)
            elif command.kind == "alert":
                await self._safety_plan(
                    "alert_caregiver",
                    {"incident_id": command.incident_id, "severity": command.severity, "message": command.text},
                    "Message the configured trusted contact",
                    action_id=command.action_id,
                )
            elif command.kind == "resolved":
                await self._caption(self.settings.session_id, "Check-in resolved.", "normal", "safety", new_id("msg"))
        await self.sync_incident()

    async def _speak_question(self, command: SafetyCommand) -> None:
        assert self.safety is not None
        if self.http is None:
            self.safety.on_audio_unavailable(command.message_id, self.clock.now())
            return
        speech_on = any(item.get("provider") == "elevenlabs" and item.get("readiness") in {"configured", "ready"} for item in self.integrations)
        tool = "announce" if speech_on else "show_caption"
        body = (
            {"text": command.text, "room": self.safety.incident.room}
            if tool == "announce"
            else {"text": command.text, "priority": "high", "ttl_s": 30}
        )
        try:
            response = await self.http.post(
                f"{self.settings.home_url}/api/tools/{tool}",
                json=body,
                headers={
                    **self.headers(),
                    "X-Plan-Id": "pln_safety",
                    "X-Action-Id": command.message_id,
                    "X-Session-Id": self.settings.session_id,
                },
                timeout=self.settings.home.speech_total_cap_s + 2,
            )
            effect = response.json()
        except httpx.HTTPError:
            effect = {"verification": "unverified", "status": "failed"}
        if effect.get("verification") == "playback_confirmed":
            self.safety.on_playback_ended(command.message_id, self.clock.now())
        else:
            self.safety.on_audio_unavailable(command.message_id, self.clock.now())
        await self.emit(
            self.settings.session_id,
            "question.opened",
            {
                "question_id": command.question_id,
                "text": command.text,
                "choices": ["im_okay", "need_help"],
                "incident_id": command.incident_id,
                "audio": effect.get("status"),
            },
        )

    async def _safety_plan(self, tool: str, args: dict, intent: str, action_id: str | None = None) -> dict:
        assert self.safety and self.safety.incident
        observation = Observation(
            observation_id=new_id("obs"),
            session_id=self.settings.session_id,
            kind="state",
            captured_at=self.clock.now(),
            room=self.safety.incident.room,
            supports=["safety_workflow"],
            targets=[self.safety.incident.room],
            summary=intent,
            source="fixture" if self.safety.incident.source == "fixture" else "live",
        )
        self.remember_observation(observation)
        submission = PlanSubmission(
            schema_version=SCHEMA_VERSION,
            session_id=self.settings.session_id,
            intent=intent,
            trigger={"kind": "safety", "event": "possible_fall", "room": self.safety.incident.room},
            actions=[
                ActionProposal(
                    action_id=action_id or new_id("act"),
                    tool=tool,
                    args=args,
                    evidence_ids=[observation.observation_id],
                )
            ],
            reasoning_summary=intent,
        )
        # Safety plans are produced by the incident manager, not by a model score.
        model = self.registry.validate_args(tool, args)
        action = PlanAction(
            action_id=submission.actions[0].action_id,
            tool=tool,
            args=canonical_args(model),
            evidence_ids=[observation.observation_id],
            action_class=self.registry.get(tool).action_class,
        )
        checks = [
            CheckOutcome(name=name, passed=True, detail="safety workflow")
            for name in (
                "cue_supported",
                "target_clear",
                "evidence_fresh",
                "context_agrees",
                "support_sufficient",
            )
        ]
        return await self._authorize_and_send(submission, [action], checks, 5, "safety_workflow")

    async def safety_tick(self) -> None:
        assert self.safety is not None
        commands = self.safety.poll(self.clock.now())
        if commands:
            await self.apply_safety(commands)

    async def open_fall(self, event: PossibleFallEvent) -> dict:
        assert self.safety is not None
        if event.session_id != self.settings.session_id:
            return _error("invalid_arguments", "session does not match")
        commands = self.safety.open_possible_fall(event, self.clock.now())
        if not commands:
            return {"status": "ignored", "message": "fall evidence did not meet the two-second floor rule, or an incident is already open"}
        await self.apply_safety(commands)
        return {"status": "opened", "incident_id": self.safety.incident.incident_id if self.safety.incident else None}

    async def help_request(self, source: str) -> dict:
        assert self.safety is not None
        room = self.settings.home.rooms[0].id
        commands = self.safety.open_help_request(self.settings.session_id, room, self.clock.now(), source)
        await self.apply_safety(commands)
        incident = self.safety.incident
        return {"status": "help", "incident_id": incident.incident_id if incident else None, "phase": incident.phase if incident else None}

    def claim_incident(self, incident_id: str) -> bool:
        assert self.safety is not None
        return self.safety.claim_send(incident_id)

    def note_manual(self, target: str) -> None:
        self.manual_until[target] = self.clock.now() + timedelta(minutes=15)

    async def proxy_state(self) -> dict:
        if self.http is None:
            return {}
        response = await self.http.get(f"{self.settings.home_url}/api/state", headers=self.headers())
        response.raise_for_status()
        return response.json()


def _targets(args: dict) -> list[str]:
    values = []
    for key in ("room", "light"):
        if args.get(key):
            values.append(str(args[key]))
    return values


def _duplicate_caption(actions: list[ActionProposal]) -> bool:
    announcements = {action.args.get("text") for action in actions if action.tool == "announce"}
    return any(action.tool == "show_caption" and action.args.get("text") in announcements for action in actions)


def _question_text(action: PlanAction) -> str:
    if action.tool == "dim_lights":
        target = action.args.get("light") or "the lights"
        return f"Dim {target} to {action.args.get('luminosity')}%?"
    return f"Apply {action.tool}?"


def _completion_text(result: PlanResult) -> str | None:
    parts: list[str] = []
    for item in result.action_results:
        if item.tool == "dim_lights" and item.status == "succeeded":
            parts.append("Brightness updated.")
        elif item.tool == "set_light_warmth" and item.status == "succeeded":
            parts.append("Light warmth updated.")
        elif item.tool == "set_blinds" and item.status == "succeeded":
            parts.append("Blinds updated.")
        elif item.tool == "flash_lights" and item.status == "succeeded":
            parts.append("Lights flashed.")
        elif item.tool == "play_sound" and item.verification == "playback_confirmed" and item.status == "succeeded":
            parts.append("Music is playing.")
        elif item.tool == "play_sound" and item.status == "unknown":
            parts.append("Music was requested, but playback is not confirmed.")
        elif item.tool == "play_sound" and item.error_code == "partial_effects":
            parts.append("The track started. Volume was not confirmed.")
        elif item.tool == "play_sound":
            parts.append("Music did not start.")
        elif item.tool == "stop_sound" and item.status == "succeeded":
            parts.append("Sound stopped.")
        elif item.tool == "set_volume" and item.verification == "playback_confirmed":
            parts.append("Volume updated.")
        elif item.tool == "alert_caregiver" and item.verification == "provider_delivered":
            parts.append("The trusted contact message was delivered.")
        elif item.tool == "alert_caregiver" and item.verification == "provider_accepted":
            parts.append("The trusted contact message was submitted.")
        elif item.tool == "alert_caregiver" and item.status == "unknown":
            parts.append("The trusted contact message outcome is unknown.")
        elif item.tool == "alert_caregiver":
            parts.append("The trusted contact message was not sent.")
    if not parts:
        return None
    return " ".join(parts)[:240]


def _error(error_code: str, message: str) -> dict:
    return {"status": "rejected", "error_code": error_code, "message": message}


def build_runtime(settings: RuntimeSettings, clock: Clock | None = None) -> Runtime:
    return Runtime(settings=settings, clock=clock or Clock(), registry=build_registry(settings.home))
