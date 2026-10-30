"""Clef, Cloudflare's decision model: the client, and Clef as the judge component.

Clef (``POST {base_url}/systemone``) takes a ``state`` and typed questions and
returns a probability per allowed answer; it writes no text.  ``ClefClient``
sends one request and logs it in full; the end-to-end Clef strategy
(``claims_pipeline/clef_e2e.py``) uses it directly.

As the judge, each judged check becomes one ``choice`` question over yes / no
/ unknown, so the tri-state verdict keeps its explicit "not stated" option.  The chosen
answer stands only when its probability reaches ``clef_min_confidence``;
below that the check is UNKNOWN.  The probability is the check's confidence
and the rationale shows all three, but there is no quote: Clef points at no
document line, so a judged check answered here has no supporting quote.

The state carries what the generative judge reads: the policy wording, the
claim description, the facts already extracted and the numbered documents.

As the scorer, Clef decides nothing; it puts a probability on what the other
components concluded, and the judge reads those probabilities:

- ``score_document``: one request per document, showing Clef the document
  itself (the image, or the file's text), never the reader's transcription.
  Two questions: which of the policy's document types it is, and whether it
  shows signs of alteration.
- ``score_facts``: one request per claim, one question per extracted fact:
  do the description and documents support this value?  Clef is not shown the
  other extracted facts, so each answer stands on the evidence alone.
"""
from __future__ import annotations

import base64
import json
import time
from typing import Any

import httpx

from claims_pipeline.prompts import clef, prompt
from claims_pipeline.settings import Settings

from .render import render_facts, render_readings
from .base import BackendError
from .calllog import CallLog
from .images import for_local_server
from .types import DocumentScoreRequest, FactScoreRequest, JudgeRequest, Judgment, Scores, Usage

# Questions and answer options live in claims_pipeline/prompts/clef.json; these names stay for
# the modules and tests that quote them.
_CLEF = json.loads(prompt("clef").text)
JUDGE_OPTIONS: dict[str, str] = _CLEF["judge_question_options"]
ALTERED_OPTIONS: dict[str, str] = _CLEF["altered_options"]
FACT_OPTIONS: dict[str, str] = _CLEF["fact_options"]


def state_of(req: JudgeRequest) -> str:
    return (f"{prompt('submission_stamp').render()}\n\n# The insurance policy\n\n{req.policy_text}\n\n# Claim description\n{req.description}\n\n"
            f"# Facts already extracted\n{render_facts(req.facts)}\n\n# Documents\n{render_readings(req.readings)}")


def read_choice(data: Any, name: str, options: dict[str, str]) -> tuple[str, dict[str, float]]:
    """The chosen option and the probability per option, from ``answers.<name>``.

    The documented shape is ``{"choice": ..., "probabilities": {...}, "confidence": ...}``;
    a reply without per-option probabilities falls back to ``confidence`` for the choice."""
    answer = (data.get("answers") or {}).get(name) if isinstance(data, dict) else None
    if isinstance(answer, str):
        answer = {"choice": answer}
    if not isinstance(answer, dict):
        raise BackendError(f"no answer for {name!r} in reply: {str(data)[:200]}")
    raw = answer.get("probabilities") or answer.get("probs") or {}
    probs = {k: float(v) for k, v in raw.items() if k in options and isinstance(v, (int, float))}
    choice = answer.get("choice")
    if choice not in options:
        if not probs:
            raise BackendError(f"answer for {name!r} has no valid choice: {str(answer)[:200]}")
        choice = max(probs, key=probs.get)
    if choice not in probs:
        confidence = answer.get("confidence")
        if not isinstance(confidence, (int, float)):
            raise BackendError(f"answer for {name!r} has no probability: {str(answer)[:200]}")
        probs[choice] = float(confidence)
    return choice, probs


def choice_question(instructions: str, options: dict[str, str]) -> dict[str, Any]:
    return {"type": "choice", "instructions": instructions, "criteria": options}


class ClefClient:
    """One ``/systemone`` request, logged in full like every other model call."""

    name = "clef"

    def __init__(self, settings: Settings | None = None, http: httpx.Client | None = None,
                 log: CallLog | None = None):
        self.settings = settings or Settings()
        base = self.settings.clef_base_url.rstrip("/")
        if not base:
            raise BackendError("CLAIMS_CLEF_BASE_URL is not set")
        headers = {"Content-Type": "application/json"}
        if self.settings.clef_api_key:
            headers["Authorization"] = f"Bearer {self.settings.clef_api_key}"
        self.base_url = base
        self.http = http or httpx.Client(base_url=base, headers=headers,
                                         timeout=httpx.Timeout(self.settings.clef_timeout_s, connect=20.0))
        self.log = log if log is not None else (CallLog(self.settings.trace_dir) if self.settings.trace else None)

    def ask(self, component: str, state: str, questions: dict[str, Any],
            images: list[str] | None = None) -> tuple[dict[str, Any], Usage]:
        """The raw reply and its usage; ``images`` are data URLs (Clef reads up to four)."""
        body: dict[str, Any] = {"model": self.settings.clef_model, "state": state, "questions": questions}
        if images:
            body["images"] = images
        usage = Usage(model=self.settings.clef_model)
        t0 = time.perf_counter()
        rec: dict[str, Any] = {"response": None, "error": None}
        try:
            try:
                r = self.http.post("/systemone", json=body)
            except httpx.HTTPError as exc:
                raise BackendError(f"connection error: {exc}") from exc
            try:
                data = r.json()
            except ValueError:
                data = {"status": r.status_code, "text": r.text}
            rec["response"] = data
            if r.status_code >= 400:
                raise BackendError(f"API error {r.status_code}: {r.text[:300]}")
            if not isinstance(data, dict) or not isinstance(data.get("answers"), dict):
                raise BackendError(f"no answers in reply: {str(data)[:200]}")
            u = data.get("usage") or {}
            usage.input_tokens = int(u.get("prompt_tokens") or u.get("input_tokens") or 0)
        except BackendError as exc:
            rec["error"] = str(exc)
            usage.call_id = self._log(component, body, usage, rec, t0)
            exc.usage = usage
            raise
        usage.call_id = self._log(component, body, usage, rec, t0)
        return data, usage

    def _log(self, component: str, body: dict[str, Any], usage: Usage, rec: dict[str, Any], t0: float) -> str | None:
        if self.log is None:
            return None
        response = rec["response"]
        answers = response.get("answers") if isinstance(response, dict) else None
        return self.log.record(component=component, provider="clef", endpoint=f"{self.base_url}/systemone",
                               model=usage.model, request=body, parsed=answers,
                               usage=usage.model_dump(exclude={"call_id"}),
                               latency_ms=int((time.perf_counter() - t0) * 1000), **rec)


class ClefJudge:
    """``judge`` on Clef; the other components stay on the main backend."""

    name = "clef"

    def __init__(self, settings: Settings | None = None, client: ClefClient | None = None):
        self.settings = settings or Settings()
        self.client = client or ClefClient(self.settings)
        self.last_usage: Usage | None = None

    def judge(self, req: JudgeRequest) -> Judgment:
        min_conf = self.settings.clef_min_confidence
        options = clef()["judge_question_options"]
        data, self.last_usage = self.client.ask(
            "judge", state_of(req), {req.check_id: choice_question(req.question, options)})
        try:
            choice, probs = read_choice(data, req.check_id, JUDGE_OPTIONS)
        except BackendError as exc:
            exc.usage = self.last_usage
            raise
        p = probs[choice]
        answer = choice if p >= min_conf else "unknown"
        shown = ", ".join(f"{k} {probs[k]:.2f}" for k in JUDGE_OPTIONS if k in probs)
        held = "" if answer == choice else f"; '{choice}' is below the {min_conf:.2f} floor"
        return Judgment(answer=answer, rationale=f"Clef probabilities: {shown}{held}.", confidence=p)


class ClefScorer:
    """``score_document`` and ``score_facts`` on Clef: probabilities, never conclusions."""

    name = "clef"

    def __init__(self, settings: Settings | None = None, client: ClefClient | None = None):
        self.settings = settings or Settings()
        self.client = client or ClefClient(self.settings)
        self.last_usage: Usage | None = None

    def score_document(self, req: DocumentScoreRequest) -> dict[str, Scores]:
        r = req.read
        images = None
        if r.image is not None:
            data, media_type = for_local_server(r.image, r.media_type)
            images = [f"data:{media_type};base64,{base64.standard_b64encode(data).decode()}"]
            body = "(the document is the attached image)"
        else:
            body = r.text or "(empty)"
        state = f"# Document: {r.filename}\n\n{body}"
        types = dict(r.doc_types)
        c = clef()
        questions = {
            "doc_type": choice_question(c["doc_type_question"], types),
            "altered": choice_question(c["altered_question"], c["altered_options"]),
        }
        data, self.last_usage = self.client.ask("score_document", state, questions, images)
        return self._read(data, {"doc_type": types, "altered": c["altered_options"]})

    def score_facts(self, req: FactScoreRequest) -> dict[str, Scores]:
        state = (f"{prompt('submission_stamp').render()}\n\n# The insurance policy\n\n{req.policy_text}\n\n# Claim description\n"
                 f"{req.description}\n\n# Documents\n{render_readings(req.readings)}")
        c = clef()
        questions = {
            f.name: choice_question(c["fact_question"].format(label=f.name.replace("_", " "), hint=f.hint, value=f.value),
                                    c["fact_options"])
            for f in req.facts
        }
        data, self.last_usage = self.client.ask("score_facts", state, questions)
        return self._read(data, {f.name: c["fact_options"] for f in req.facts})

    def _read(self, data: Any, asked: dict[str, dict[str, str]]) -> dict[str, Scores]:
        """Every question answered; one missing answer leaves that one unscored."""
        scores = {}
        for name, options in asked.items():
            try:
                scores[name] = read_choice(data, name, options)[1]
            except BackendError:
                continue
        if not scores:
            exc = BackendError(f"no usable answers in reply: {str(data)[:200]}")
            exc.usage = self.last_usage
            raise exc
        return scores
