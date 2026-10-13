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

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

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
    Generate a dynamic system prompt by combining the bot's base prompt
    with instructions from the selected persona for this conversation.

    Args:
        bot: Bot instance with prompt
        selected_persona: Persona instance selected for this conversation (can be None)

    Returns:
        str: Combined system prompt
    """
    try:
        # Start with the bot's base prompt
        system_prompt = bot.prompt.strip() if bot.prompt else ""

        # Add selected persona instructions if available
        if (
            selected_persona
            and hasattr(selected_persona, "name")
            and hasattr(selected_persona, "instructions")
        ):
            # Combine base prompt with persona instructions
            if system_prompt:
                system_prompt += "\n\n"

            system_prompt += f"Additional personality instructions:\nPersona '{selected_persona.name}': {selected_persona.instructions}"

        # Add the participant's pre-conversation survey answers, when this
        # bot is configured to use them. Renders to "" otherwise, which covers
        # both an opted-out bot and a participant whose answers never arrived.
        survey_block = render_survey_context(
            bot.survey_context_preamble, survey_context
        )
        if survey_block:
            if system_prompt:
                system_prompt += "\n\n"
            system_prompt += survey_block

        return system_prompt
    except Exception as e:
        logger.error(f"Error generating system prompt: {e}")
        # Fallback to just the bot's prompt
        return bot.prompt.strip() if bot.prompt else ""


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
