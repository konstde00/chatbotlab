"""
Compile everything a chat round sends to the model into one typed value.

The chat and follow-up paths both build a system prompt, trim the history and
call Kani. compile_prompt() is the single place that gathers those inputs, so
what was sent can be serialized whole (to_json) and what is recorded
(history_json) is, by construction, what Kani was given.
"""

import json
import logging
from dataclasses import asdict, dataclass
from typing import Literal
from xml.sax.saxutils import escape

from kani import ChatMessage, ChatRole

from .survey import render_survey_context

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Turn:
    role: Literal["user", "assistant"]
    content: str


@dataclass(frozen=True)
class PersonaSnapshot:
    name: str
    instructions: str


@dataclass(frozen=True)
class CompiledPrompt:
    base_prompt: str
    persona: PersonaSnapshot | None
    survey_context: list[dict] | None
    system_prompt: str
    # Prior turns only. The new message travels separately as `query`, because
    # Kani.full_round() appends the query to the history itself.
    history: tuple[Turn, ...]
    query: str

    def to_dict(self) -> dict:
        """The Utterance.compiled_prompt value."""
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    def kani_history(self) -> list[ChatMessage]:
        return [
            ChatMessage(
                role=ChatRole.USER if t.role == "user" else ChatRole.ASSISTANT,
                content=t.content,
            )
            for t in self.history
        ]

    def history_json(self) -> str:
        """The Utterance.chat_history_used value: the history Kani was given."""
        return json.dumps([asdict(t) for t in self.history], indent=2)


def generate_system_prompt(bot, selected_persona=None, survey_context=None):
    """
    Render the system prompt sent to the model, following the standard in
    docs/llm-integration/system-prompt-format.rst:

        <instructions>   the bot's prompt, word for word
        <persona>        the conversation's persona, if it has one
        <survey_context> the participant's survey answers, if any apply

    Blocks come in that fixed order, separated by a blank line, and a block
    with nothing to say is omitted rather than left empty. Admin-written text
    is trusted and passed unescaped; participant-written answers are escaped
    by render_survey_context().
    """
    instructions = (bot.prompt or "").strip()
    blocks = (
        [f"<instructions>\n{instructions}\n</instructions>"] if instructions else []
    )
    try:
        if selected_persona:
            name = escape(selected_persona.name, {'"': "&quot;"})
            blocks.append(
                f'<persona name="{name}">\n'
                f"{selected_persona.instructions.strip()}\n</persona>"
            )
        survey_block = render_survey_context(
            bot.survey_context_preamble, survey_context
        )
        if survey_block:
            blocks.append(survey_block)
    except Exception as e:
        # Mid-conversation is the worst place to fail: fall back to the
        # instructions block alone rather than erroring the round.
        logger.error(f"Error generating system prompt: {e}")
        blocks = blocks[:1]
    return "\n\n".join(blocks)


def compile_prompt(bot, persona, survey_context, history, query):
    """
    Pure. `history` is the already-trimmed list of {"role", "content"} dicts,
    not including `query`.
    """
    return CompiledPrompt(
        base_prompt=(bot.prompt or "").strip(),
        persona=PersonaSnapshot(persona.name, persona.instructions)
        if persona
        else None,
        survey_context=survey_context,
        system_prompt=generate_system_prompt(bot, persona, survey_context),
        history=tuple(
            Turn("user" if m["role"] == "user" else "assistant", str(m["content"]))
            for m in history
        ),
        query=query,
    )
