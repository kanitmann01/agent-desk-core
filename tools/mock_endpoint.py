"""A local stand-in for the desk's BYOK endpoint, for demos and tests.

The desk's one model client speaks the OpenAI-compatible chat shape
against any base URL the user saves in settings. This tool is that shape
on the loopback: it answers /chat/completions with deterministic text
built from the prompt, so the full memory loop (run, recall, feedback,
capture, review, recall again) can be demonstrated end to end without a
real key, and the same walkthrough is repeatable on any machine.

It is a test double, not a model. What it does is honest about what the
host sent: an agent prompt's reply echoes the bounded known-facts block
the host injected (so recall is visible in the artifact), and an
extraction prompt's reply returns the user's own feedback sentence as
the fact candidate (what a careful extraction model would pick). The
words are canned; the plumbing they ride is the real one.

Run it, then point the desk's endpoint at http://127.0.0.1:<port>/v1:

    python tools/mock_endpoint.py --port 8788
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# The stable markers the reply builder keys on: the extraction system
# prompt's own first sentence, and the agent prompt's instructions label.
_EXTRACTION_MARKER = "durable memory facts"
_AGENT_MARKER = "agent instructions:"
_FEEDBACK_MARKER = "How it landed (the user's own words): "
_BRIEF_MARKER = "The user's brief (they will not restate their context; use it):"
_FACTS_HEADER_MARKER = "Known facts about the user"
# The skill-refine prompt's own labels (repetition/refine.py build_input).
_SKILL_REFINE_MARKER = "Refine one saved skill of this desk."
_SKILL_PROMPT_MARKER = "The skill's current prompt:"
_SKILL_FEEDBACK_MARKER = "The user's feedback:"

# Names the entity miner refuses: sentence starters and calendar words,
# the rules extractor's own stoplist spirit, so "My manager Dana" yields
# Dana alone.
_NOT_NAMES = {
    "i", "we", "you", "they", "he", "she", "it", "my", "our", "the",
    "a", "an", "this", "that", "monday", "tuesday", "wednesday",
    "thursday", "friday", "saturday", "sunday", "today", "tomorrow",
    "yesterday", "q1", "q2", "q3", "q4", "the", "and", "but", "so",
}

# The model ids /models lists. A caller that saves a connection against
# this mock picks from these, so a readiness check can prove the model it
# chose is the model each request carried.
MODELS = ("mock-chat", "mock-vision")

# The model name of every chat request this process has answered, in
# order. A test or tool that runs the mock in-process reads it to assert
# what the desk really sent.
SEEN_MODELS: list[str] = []

# The line a user types to hand the desk one fact by hand: what a careful
# extraction model would pick out of an assistant turn.
_REMEMBER_MARKER = "Remember this:"


def _known_facts(prompt: str) -> list[str]:
    """The '- ' fact lines under the known-facts header, in order.

    The host's bounded block is the only channel memory reaches the
    prompt through, so echoing these lines back is echoing exactly what
    the desk recalled for this call.
    """
    facts: list[str] = []
    in_block = False
    for line in prompt.splitlines():
        if _FACTS_HEADER_MARKER in line:
            in_block = True
            continue
        if in_block:
            if not line.strip():
                break
            if line.startswith("- "):
                facts.append(line[2:].strip())
    return facts


def _after(prompt: str, marker: str) -> str:
    """The text after the marker's line, or the empty string."""
    if marker not in prompt:
        return ""
    tail = prompt.split(marker, 1)[1]
    return tail.lstrip(" \n").strip()


def _names_in(sentence: str) -> list[dict]:
    """Capitalized words as person entities, the draft cards' own spirit."""
    seen: list[str] = []
    for word in re.findall(r"[A-Z][a-z]+", sentence):
        if word.lower() in _NOT_NAMES or word in seen:
            continue
        seen.append(word)
    return [{"name": word, "type": "person"} for word in seen]


def _extraction_reply(prompt: str) -> str:
    """One fact candidate: the user's own feedback sentence, verbatim."""
    feedback = ""
    for line in prompt.splitlines():
        if _FEEDBACK_MARKER in line:
            feedback = line.split(_FEEDBACK_MARKER, 1)[1].strip()
            break
    if not feedback:
        # An assistant turn: a "Remember this: ..." line becomes the one
        # fact a careful extraction model would keep, and nothing else does.
        handed = re.search(r"Remember this:\s*(.+)", prompt)
        if handed is None:
            return json.dumps({"facts": []})
        feedback = handed.group(1).strip()
    fact = {"text": feedback, "kind": "world", "entities": _names_in(feedback)}
    return json.dumps({"facts": [fact]})


def _outline_reply(prompt: str) -> str:
    """A plain-text briefing, for kinds that answer in outlines."""
    brief = _after(prompt, _BRIEF_MARKER) or "an untitled brief"
    facts = _known_facts(prompt)
    lines = [f"Headline: {' '.join(brief.split())[:120]}", "- The watched numbers, read once", "- The one change worth acting on"]
    for fact in facts:
        lines.append(f"- Grounded in your known facts: {fact}")
    return "\n".join(lines)


def _presentation_plan(prompt: str) -> str:
    """Echo the Sections JSON the prompt already listed, with a real title."""
    line = next((item for item in prompt.splitlines() if item.startswith("Sections: ")), "")
    try:
        sections = json.loads(line.split("Sections: ", 1)[1])
    except (ValueError, IndexError):
        sections = []
    topic = next((item for item in prompt.splitlines() if item.startswith("Topic: ")), "Topic: Briefing")
    topic = topic.split("Topic: ", 1)[1].strip()
    return json.dumps({"title": _deck_title(topic), "sections": sections})


def _deck_title(topic: str) -> str:
    """Title case for a lowercase topic. QBR stays an abbreviation."""
    words = topic.split()
    if words and words[0].lower() == "the":
        words = words[1:]
    titled = []
    for word in words:
        core = word.strip(".,").lower()
        if core == "qbr":
            titled.append("QBR")
        elif core == "competitor-brief":
            titled.append("Competitor Brief")
        else:
            titled.append(word[:1].upper() + word[1:])
    return " ".join(titled) or "Briefing"


def _presentation_fill(prompt: str) -> str:
    """Two slides for the named section, with a figure only when a source has one."""
    section = "This section"
    for line in prompt.splitlines():
        if line.startswith("Section: "):
            section = line.split("Section: ", 1)[1].strip() or section
            break
    numbers = re.findall(r"\d+", prompt.split("Sources:", 1)[-1])
    figure = (
        f"The source says {numbers[0]}, and that is the only figure this section uses."
        if numbers else ""
    )
    first_title, second_title = _slide_titles(section)
    first = [
        figure or (
            f"{section} starts from the brief, not from a figure the desk did not supply, "
            "and a listener should be able to restate that in one breath."
        ),
        "The situation includes what did not change and what is still open, so the slide is not only the good news.",
        "The constraint on the next step stays here, next to the decision it limits.",
    ]
    second = [
        f"One example makes {section.lower()} easier to retell, and it belongs to this section so later slides do not echo it.",
        "What the room should remember without the notes is the choice, the reason, and the thing we are deliberately not doing.",
    ]
    return json.dumps({"slides": [
        {"title": first_title, "bullets": first, "citations": [1] if numbers else []},
        {"title": second_title, "bullets": second, "citations": []},
    ]})


def _slide_titles(section: str) -> tuple[str, str]:
    """Two titles that are not the section name with a prefix."""
    own = {
        "context": ("Where things stand", "The decision in front of us"),
        "findings": ("What turned up", "What follows from that"),
        "what we do next": ("The next move", "What we will leave"),
        "snapshot": ("The quarter on one page", "What the quarter is not"),
        "performance": ("How the work went", "Where it slowed"),
        "wins": ("What held", "What we can repeat"),
        "risks": ("What could slip", "What we would notice first"),
        "asks": ("What we need", "What we can do without"),
        "landscape": ("The field", "Where we sit in it"),
        "profile": ("Who this is", "How they compete"),
        "gaps": ("What is missing", "What that costs"),
        "implications": ("What it means for us", "What it does not mean"),
        "moves": ("The move", "The move after that"),
    }
    return own.get(section.lower(), (section, f"The point of {section}"))


def _skill_refine_reply(prompt: str) -> str:
    """The refined prompt as strict JSON: the current prompt with the
    queued feedback appended as instructions, what a careful refine
    model would return for the same input. The user's words are echoed
    verbatim, the extraction reply's own spirit."""
    current = _after(prompt, _SKILL_PROMPT_MARKER)
    current = current.split(_SKILL_FEEDBACK_MARKER, 1)[0].strip()
    feedback_lines = []
    in_block = False
    for line in prompt.splitlines():
        if _SKILL_FEEDBACK_MARKER in line:
            in_block = True
            continue
        if in_block and line.startswith("- "):
            feedback_lines.append(line[2:].strip())
    additions = "".join(f"\n- {item}" for item in feedback_lines)
    refined = (current + "\n\nUser feedback applied:" + additions).strip()
    return json.dumps({"prompt": refined})


def reply_for(prompt: str) -> str:
    """The deterministic answer for one prompt, by its stable markers.

    The kind is read from the agent-instructions label, never from loose
    words in the prompt.
    """
    if _EXTRACTION_MARKER in prompt:
        return _extraction_reply(prompt)
    if _SKILL_REFINE_MARKER in prompt:
        return _skill_refine_reply(prompt)
    if _AGENT_MARKER in prompt:
        return _outline_reply(prompt)
    if "Presentation plan" in prompt:
        return _presentation_plan(prompt)
    if "Presentation fill" in prompt:
        return _presentation_fill(prompt)
    return "The mock endpoint answers a plain prompt with this sentence."


class MockEndpointHandler(BaseHTTPRequestHandler):
    """One route, the chat shape the desk's client already parses."""

    def do_POST(self) -> None:  # noqa: N802 - the stdlib's spelling
        """Answer /chat/completions with the deterministic reply.

        A body that asks to stream (the side-car engines do; the desk's
        own client never sets stream) gets the same reply as an OpenAI
        SSE chunk sequence, so a streaming consumer reads a complete
        turn with a finish reason instead of an empty stream."""
        if self.path.rstrip("/") != "/v1/chat/completions" and self.path.rstrip("/") != "/chat/completions":
            self._send_json(404, {"error": "the mock serves /chat/completions only"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        SEEN_MODELS.append(str(body.get("model") or ""))
        messages = body.get("messages") or []
        prompt = ""
        if messages and isinstance(messages[0], dict):
            prompt = str(messages[0].get("content") or "")
        reply = reply_for(prompt)
        print(f"mock endpoint: {len(prompt)}-char prompt -> {len(reply)}-char reply", flush=True)
        if body.get("stream"):
            self._send_stream(str(body.get("model") or "mock-chat"), reply)
            return
        self._send_json(200, {
            "choices": [{"message": {"role": "assistant", "content": reply}}],
        })

    def _send_stream(self, model: str, reply: str) -> None:
        """The reply as OpenAI SSE chunks: role, the words, stop, [DONE].

        The reply leaves in a few word-group chunks, the way a real
        endpoint streams a short sentence, and every chunk carries the
        id, object and created fields a strict client reads."""
        import time
        created = int(time.time())
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        def chunk(delta: dict, finish: str | None) -> bytes:
            """One SSE event: the chunk JSON framed as a data line."""
            payload = {
                "id": "chatcmpl-mock", "object": "chat.completion.chunk",
                "created": created, "model": model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }
            return b"data: " + json.dumps(payload).encode("utf-8") + b"\n\n"

        words = reply.split(" ")
        groups = [" ".join(words[i:i + 3]) for i in range(0, len(words), 3)]
        try:
            self.wfile.write(chunk({"role": "assistant"}, None))
            for group in groups:
                self.wfile.write(chunk({"content": group + (" " if group is not groups[-1] else "")}, None))
            self.wfile.write(chunk({}, "stop"))
            self.wfile.write(b"data: [DONE]\n\n")
        except OSError:
            pass  # the consumer hung up mid-stream; nothing to answer to

    def do_GET(self) -> None:  # noqa: N802 - the stdlib's spelling
        """The model list a connection's save-time check reads, else a refusal.

        /models answers the OpenAI list shape so the key form can offer a
        model to pick; any other path is a plain refusal, so a browser
        visit cannot look like an API call.
        """
        if self.path.rstrip("/") in ("/v1/models", "/models"):
            self._send_json(200, {"object": "list", "data": [
                {"id": model, "object": "model", "owned_by": "mock"}
                for model in MODELS
            ]})
            return
        self._send_json(404, {"error": "the mock speaks to POST /chat/completions and GET /models"})

    def _send_json(self, status: int, payload: dict) -> None:
        """One JSON answer with the right length header."""
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003 - the stdlib's hook
        """Stay quiet; the one-line print per request is the whole log."""


def main() -> int:
    """Parse the port, serve until interrupted.

    The bind is loopback only, the same rule the desk itself enforces:
    the mock echoes prompt text back to whoever called it, so it has no
    business being reachable from another machine.
    """
    parser = argparse.ArgumentParser(description="The desk's local mock BYOK endpoint")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8788)
    args = parser.parse_args()
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        parser.error(f"refusing to bind {args.host}: the mock is loopback only")
    server = ThreadingHTTPServer((args.host, args.port), MockEndpointHandler)
    print(f"mock endpoint listening on http://{args.host}:{args.port}/v1", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
