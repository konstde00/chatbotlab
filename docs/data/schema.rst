Database Schema
===============

This document describes the database schema for ChatbotLab's backend.
The database is implemented in **MySQL** and stores information about
bots, conversations, utterances, and keystrokes. Each table is prefixed
with ``chatbot_`` and linked by foreign keys for data integrity.

Bots Table
----------

**Table name:** ``chatbot_bot``

This table defines each chatbot available in the system and stores
its configuration, prompt, and assigned model.

+-----------------------+----------------+----------------------------------------------------------+
| **Column**            | **Type / Key** | **Description**                                          |
+=======================+================+==========================================================+
| ``id``                | PK             | Unique ID of the bot.                                    |
+-----------------------+----------------+----------------------------------------------------------+
| ``name``              | PK             | Descriptive name of the bot (must be unique). Use a      |
|                       |                | ``-voice`` suffix for voice-enabled versions.            |
+-----------------------+----------------+----------------------------------------------------------+
| ``prompt``            | TEXT           | Prompt describing how the bot should behave.             |
+-----------------------+----------------+----------------------------------------------------------+
| ``model_id``          | VARCHAR        | Identifier for the connected model.                      |
+-----------------------+----------------+----------------------------------------------------------+
| ``model_type``        | VARCHAR        | Type or family of the connected model (e.g., GPT-4o).    |
+-----------------------+----------------+----------------------------------------------------------+
| ``initial_utterance`` | TEXT           | Optional initial message sent when conversation starts.  |
+-----------------------+----------------+----------------------------------------------------------+




Conversations Table
-------------------

**Table name:** ``chatbot_conversation``

Stores high-level metadata for each chat session, including study context,
participant identifiers, and Qualtrics linkage.

+-----------------------+------------------+---------------------------------------------------------------+
| **Column**            | **Type / Key**   | **Description**                                               |
+=======================+==================+===============================================================+
| ``id``                | PK               | Auto-incremented unique ID for each conversation.             |
+-----------------------+------------------+---------------------------------------------------------------+
| ``conversation_id``   | PK               | Response ID from Qualtrics representing the unique session.   |
+-----------------------+------------------+---------------------------------------------------------------+
| ``bot_name``          | FK → chatbot_bot | Which bot the participant interacted with.                    |
+-----------------------+------------------+---------------------------------------------------------------+
| ``participant_id``    | VARCHAR          | Participant ID (fetched from Qualtrics or Prolific).          |
+-----------------------+------------------+---------------------------------------------------------------+
| ``study_name``        | VARCHAR          | Optional name of the study.                                   |
+-----------------------+------------------+---------------------------------------------------------------+
| ``user_group``        | VARCHAR          | Qualtrics condition or randomized group assignment.           |
+-----------------------+------------------+---------------------------------------------------------------+
| ``started_time``      | DATETIME         | Timestamp when the conversation began.                        |
+-----------------------+------------------+---------------------------------------------------------------+
| ``initial_utterance`` | TEXT             | The initial message if the bot began the conversation.        |
+-----------------------+------------------+---------------------------------------------------------------+
| ``survey_id``         | VARCHAR          | Qualtrics ``survey_id`` fetched via URL.                      |
+-----------------------+------------------+---------------------------------------------------------------+
| ``survey_meta_data``  | JSON             | Full Qualtrics metadata payload received at start.            |
+-----------------------+------------------+---------------------------------------------------------------+

---

Utterances Table
----------------

**Table name:** ``chatbot_utterance``

Each row corresponds to a single message (utterance) in a conversation.

+---------------------+------------------+--------------------------------------------------------------+
| **Column**          | **Type / Key**   | **Description**                                              |
+=====================+==================+==============================================================+
| ``id``              | PK               | Unique ID for each utterance.                                |
+---------------------+------------------+--------------------------------------------------------------+
| ``speaker_id``      | VARCHAR          | Identifier of the message speaker (bot or participant).      |
+---------------------+------------------+--------------------------------------------------------------+
| ``bot_name``        | VARCHAR          | Name of the bot in this conversation.                        |
+---------------------+------------------+--------------------------------------------------------------+
| ``conversation_id`` | FK → chatbot_conversation | Conversation foreign key.                           |
+---------------------+------------------+--------------------------------------------------------------+
| ``participant_id``  | VARCHAR          | Qualtrics or Prolific participant ID.                        |
+---------------------+------------------+--------------------------------------------------------------+
| ``text``            | TEXT             | Content of the utterance.                                    |
+---------------------+------------------+--------------------------------------------------------------+
| ``created_time``    | DATETIME         | When the utterance was created.                              |
+---------------------+------------------+--------------------------------------------------------------+
| ``audio_file``      | VARCHAR (URL)    | Link to audio file stored in S3 (if applicable).             |
+---------------------+------------------+--------------------------------------------------------------+
| ``is_voice``        | BOOLEAN          | Indicates whether the utterance includes voice (1 = True).   |
+---------------------+------------------+--------------------------------------------------------------+
| ``instruction_prompt`` | TEXT          | System prompt (bot prompt + persona) sent to the LLM.        |
+---------------------+------------------+--------------------------------------------------------------+
| ``chat_history_used`` | TEXT (JSON)    | Chat history that was actually passed to the LLM.            |
+---------------------+------------------+--------------------------------------------------------------+
| ``compiled_prompt`` | JSON             | Everything sent to the LLM: base prompt, persona, survey     |
|                     |                  | context, history, query and the rendered system prompt.      |
+---------------------+------------------+--------------------------------------------------------------+
| ``moderation_category`` | VARCHAR      | Moderation category that blocked this exchange; ``NULL``     |
|                     |                  | when the message was not blocked.                            |
+---------------------+------------------+--------------------------------------------------------------+
| ``moderation_scores`` | JSON           | Full category→score map for a blocked message. Recorded on   |
|                     |                  | the participant row only; ``NULL`` otherwise.                |
+---------------------+------------------+--------------------------------------------------------------+

.. note::

   When a message is blocked, no LLM call is made. Two rows are written: the
   participant's message and a fixed warning reply. Both carry
   ``moderation_category``, so ``WHERE moderation_category IS NOT NULL``
   returns the complete exchange. To exclude canned warnings from a transcript,
   drop the assistant rows where that column is set. Blocks that predate this
   column are labelled ``unknown`` — their true category was never recorded.

---

Keystrokes Table
----------------

**Table name:** ``chatbot_keystroke``

Captures timing and engagement metrics for each chat session, providing
context about user attention and typing behavior.

+------------------------------+---------------------------+-------------------------------------------------------------+
| **Column**                   | **Type / Key**            | **Description**                                             |
+==============================+===========================+=============================================================+
| ``id``                       | PK                        | Unique ID for each keystroke record.                        |
+------------------------------+---------------------------+-------------------------------------------------------------+
| ``conversation_id``          | FK → chatbot_conversation | Conversation foreign key.                                   |
+------------------------------+---------------------------+-------------------------------------------------------------+
| ``total_time_on_page``       | FLOAT / INT               | Time (in seconds) the participant spent on the chat page.   |
+------------------------------+---------------------------+-------------------------------------------------------------+
| ``text_time_away_from_page`` | FLOAT / INT               | Time (in seconds) the participant was away from the page.   |
+------------------------------+---------------------------+-------------------------------------------------------------+
| ``keystroke_count``          | INT                       | Number of keys pressed during the chat.                     |
+------------------------------+---------------------------+-------------------------------------------------------------+
| ``timestamp``                | DATETIME                  | When the keystroke data was recorded.                       |
+------------------------------+---------------------------+-------------------------------------------------------------+

---

Schema Relationships
--------------------

- Each **conversation** is linked to exactly one **bot** via ``bot_name``.  
- Each **utterance** belongs to one **conversation** (``conversation_id``).  
- Each **keystroke** record belongs to one **conversation** (``conversation_id``).  
- Participants and studies can be linked across sessions by ``participant_id`` or ``study_name``.

.. mermaid::

   erDiagram
      chatbot_bot ||--o{ chatbot_conversation : "bot_name (FK)"
      chatbot_conversation ||--o{ chatbot_utterance : "conversation_id (FK)"
      chatbot_conversation ||--o{ chatbot_keystroke : "conversation_id (FK)"

      chatbot_bot {
        int id PK
        varchar name
        text prompt
        varchar model_id
        varchar model_type
        text initial_utterance
      }

      chatbot_conversation {
        int id PK
        varchar conversation_id
        varchar bot_name FK
        varchar participant_id
        varchar study_name
        varchar user_group
        datetime started_time
        text initial_utterance
        varchar survey_id
        json survey_meta_data
      }

      chatbot_utterance {
        int id PK
        varchar speaker_id
        varchar bot_name
        varchar conversation_id FK
        varchar participant_id
        text text
        datetime created_time
        varchar audio_file
        boolean is_voice
      }

      chatbot_keystroke {
        int id PK
        varchar conversation_id FK
        float total_time_on_page
        float text_time_away_from_page
        int keystroke_count
        datetime timestamp
      }

