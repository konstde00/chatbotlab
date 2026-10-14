System Prompt Format
====================

Every bot's system prompt is built from the same structure, so you can tell
exactly what the model saw for any message, in any study. This page is the
standard; the code (``chatbot/services/prompt.py``) and its tests follow it.

Two layers
----------

``Utterance.compiled_prompt`` (JSON) — the record
   Everything sent to the model for that message: the bot's prompt, the
   persona, the survey answers, the chat history, the new message, and the
   rendered system prompt. This is the source of truth for analysis and
   auditing.

``Utterance.instruction_prompt`` (text) — what the model reads
   The system prompt rendered from that record. It is generated, never
   written by hand, so the two always agree.

The rendered system prompt
--------------------------

The system prompt is made of up to three tagged blocks, always in this order
and separated by one blank line:

.. code-block:: xml

   <instructions>
   The bot's prompt, exactly as written in the admin panel.
   </instructions>

   <persona name="Warm">
   The persona's instructions.
   </persona>

   <survey_context>
   <preamble>The participant previously wrote:</preamble>
   <answers>
   [
     {
       "question": "Describe a recent situation where you needed support.",
       "answer": "My roommate and I had a fight about..."
     }
   ]
   </answers>
   </survey_context>

Rules
-----

1. **Fixed order:** ``instructions``, then ``persona``, then
   ``survey_context``.
2. **Omit, don't leave empty.** A block with nothing to say is left out
   entirely. A bot with no persona has no ``<persona>`` block; a bot with
   survey context switched off, or a participant whose answers never arrived,
   has no ``<survey_context>`` block. A blank preamble omits ``<preamble>``.
3. **Admin text is passed word for word.** The bot's prompt, persona
   instructions and preamble are written by researchers and are not escaped,
   so tags or formatting in them reach the model unchanged.
4. **Participant text is data.** Survey answers are serialized as JSON in
   the order they were asked, with ``<`` escaped as ``<``, so nothing a
   participant types can close a tag or pose as instructions. Blank answers
   are dropped.
5. **No placeholders.** Prompts never contain template tokens to fill in.
   Context the bot needs goes in its own block, so a bot's prompt reads the
   same in the admin panel as it does to the model.

Writing prompts for this format
-------------------------------

Refer to the blocks by name when your instructions depend on them, e.g. *"Use
what the participant wrote in the survey context when it is relevant."* Put
introductory wording for survey answers in the bot's **Survey context
preamble** field, not in the prompt.

Why tagged blocks, with JSON for the data
-----------------------------------------

Both providers recommend clearly delimited sections for prompts that mix
instructions with data: Anthropic recommends XML tags, and OpenAI found XML
performs well while large JSON documents perform poorly in long contexts.
JSON is kept for what it is good at — structured data (the answers) and the
stored record.

- `Anthropic: Claude prompting best practices
  <https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices>`_
- `OpenAI: GPT-4.1 prompting guide
  <https://developers.openai.com/cookbook/examples/gpt4-1_prompting_guide>`_

Changing the format
-------------------

The exact output is pinned by tests
(``test_full_prompt_follows_the_standard`` and
``test_renders_the_standard_block``). Changing the format means updating this
page and those tests together, and noting it in the release, since prompts
before and after the change are not directly comparable.
