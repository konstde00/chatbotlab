Qualtrics Integration
=====================

Overview
--------

Use ChatbotLab to embed conversational tasks into your Qualtrics survey pages.

.. important::

   Note that your Qualtrics account must be able to edit JavaScript. 
   Some institutions disable this for security reasons. Contact your Qualtrics 
   administrator if iframes are not permitted.

Data Needed to Embed Bot
------------------------

ChatbotLab Domain
^^^^^^^^^^^^^^^^^

Deploy ChatbotLab first, following the :doc:`/deployment/index` guide. You
will then have a domain name for your chatbot. Use it in place of
``<YOUR-CHATLAB-DOMAIN>`` below.

Survey ID
^^^^^^^^^

1. Navigate to your survey.
2. Click on Distributions.
3. Click on **Anonymous link**. If needed you can generate one.
4. Copy the survey ID from the link. It typically starts with ``SV_`` (for example, ``SV_cBaJiOettfQqZRY``).
5. Replace ``<SURVEY-ID>`` with your survey ID in the code below.


Bot Name
^^^^^^^^

1. Navigate to the ChatbotLab admin panel and login. 
2. On the left, under CHATBOT, click on `Bots`.
3. You can use the search bar to find your bot.
4. Copy the name under the NAME column.
5. Replace ``<BOT-NAME>`` with your bot name in the code below.

Alternatively, this could be created in your survey flow and saved to an Embedded Data
field. For example, if your embedded data field was called ``model`` then you would
replace ``<BOT-NAME>`` with ``${e://Field/model}`` in the code below. This allows you
to build sophisticated survey flows and serve different bots depending on how your
participants answer survey questions.

.. important::

   This **must** exactly match the bot name in ChatbotLab's database. 

Study Name
^^^^^^^^^^

This is a descriptive name for your study, which will be saved in ChatbotLab's. For example, 
if you study was related to a therapy bot, you could call the study ``therapy_bot``. We
recommend keeping the name simple, yet descriptive, so that you can distinguish 
between multiple studies. Replace ``<STUDY-NAME>`` with your name in the code below.

Participant ID (Optional)
^^^^^^^^^^^^^^^^^^^^^^^^^

You may also have an ID for your participant saved in an Embedded Data field. This
could be a Prolific or Mturk Worker ID. If so, you can replace ``<PARTICIPANT-ID>`` with
your embedded data, for example ``${e://Field/pid}`` if your embedded data field is ``pid``.
If you do not have this then you can set this value as the Response ID ``${e://Field/ResponseID}``,
i.e., a unique identifier for each row in your survey data.

Embedding ChatbotLab
---------------------

1. Deploy ChatbotLab on AWS following the :doc:`/deployment/index` guide.
2. In Qualtrics, add a **Text / Graphic** question.
3. Open the JavaScript editor. This is typically on the left under Edit Question -> Question Behavior
4. Add the following code in the editor under Edit Question JavaScript:

   .. literalinclude:: qualtrics/embed_chatbot.js
      :language: javascript

   This file also lives in the repository at
   ``docs/survey-integration/qualtrics/embed_chatbot.js`` so you can copy it
   directly.

   .. warning::

      **Do not use jQuery.** Qualtrics does not load it in every theme, and
      where it is missing the script dies with
      ``ReferenceError: jQuery is not defined`` before the iframe is created.
      The page then looks exactly as though no JavaScript ran at all. The
      snippet above uses plain DOM for this reason.

      If the chatbot does not appear, open the browser console. The snippet
      logs the URL it builds, and there are three common outcomes:

      - ``ReferenceError: jQuery is not defined`` — an older snippet is still
        in the question. Replace it with the one above.
      - The URL is logged with an empty ``participant_id`` — the piping did
        not resolve, so the chat never starts. Check the field name, and note
        that ``ResponseID`` may be empty in Preview; use a real survey link.
      - The URL is logged and works when opened in its own browser tab, but no
        iframe appears on the page — the element was appended too early. The
        snippet uses ``addOnReady`` rather than ``addOnload`` to avoid this,
        since Qualtrics discards elements added before the question renders.

5. Replace the placeholders:

   - ``<BOT-NAME>``: your bot's name
   - ``<STUDY-NAME>``: your chosen study label
   - ``<SURVEY-ID>``: your unique Qualtrics survey identifier
   - ``<YOUR-CHATLAB-DOMAIN>``: your ChatbotLab domain, from deployment
   - ``<PARTICIPANT-ID>`` (optional)
6. You may also want to add instructions on the task to your **Text / Graphic** question.
7. Save and preview the form to verify that the chat window loads correctly.

Passing Data
------------

You can send other data to ChatbotLab's backend database by adding additional variables and 
appending them to ``botURL``.

   .. code-block:: javascript

         var someSurveyQuestion = "${e://Field/some-question}"; 
         ...
         botURL += "?survey_question_response=" + encodeURIComponent(someSurveyQuestion);

Note that the entirety of ``botURL`` is saved as a raw string in ChatbotLab's backend,
which allows you to send arbitrary amounts of data from your survey without modifying
the database structure (i.e., you can parse variables from the raw string at a later date).

Sending Survey Answers to the Bot
---------------------------------

The ``Passing Data`` section above appends values to the chatbot URL, which
stores them for later analysis but does not show them to the bot. If you want
the bot to *read* a participant's earlier answers during the conversation —
for example, a condition where the bot knows what the participant said they
were stressed about — send them to ChatbotLab's survey endpoint instead.

This path has no length limit and no fixed number of questions, and the
answers never appear in the participant's URL.

Step 1 — Issue a token
^^^^^^^^^^^^^^^^^^^^^^

The endpoint is authenticated, and **rejects every request unless a token is
configured**. Issue one from the admin panel — no redeploy or AWS access
needed:

1. Open **Survey ingest tokens** in the admin panel and click **Add**.
2. Write a note saying which study the token is for, e.g.
   ``Stress study, wave 1``. The note is only for your own reference.
3. Save. The token is generated for you and shown under **Token** on the
   next screen — copy it from there.

Issue a separate token per study. When a study finishes, open its token and
untick **is active** to revoke it immediately; the record stays for your audit
trail.

.. warning::

   Anyone holding a token can submit survey answers for any participant, so
   treat it like a password. Do not paste it into anything participants can
   see — it belongs in the Qualtrics Web Service header, which runs
   server-side, never in survey JavaScript.

.. note::

   Deployments that would rather manage the secret as infrastructure can set
   the ``SURVEY_INGEST_TOKEN`` environment variable instead (for Terraform,
   ``survey_ingest_token`` in ``terraform.tfvars`` or the matching GitHub
   Actions secret; for local development, ``api/.env``). Both work at the same
   time, so an environment-managed token and admin-issued tokens can coexist.

Step 2 — Add a Web Service element to your survey flow
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

In Qualtrics, open **Survey Flow** and add a **Web Service** element.

.. important::

   Place the Web Service element **after** the questions you want to send and
   **before** the block containing the chatbot. Qualtrics executes flow
   elements in order, so a Web Service placed after the chatbot will deliver
   the answers too late for the conversation to use them.

Configure it as:

- **URL:** ``https://<your-chatbotlab-domain>/api/survey_response/``
- **Method:** ``POST``
- **Body:** ``application/json``
- **Header:** ``X-Survey-Token`` set to the token from Step 1

The JSON body:

   .. important::

      **Put each question in its own String body parameter.** Qualtrics escapes
      a String parameter correctly, but substitutes piped text *verbatim* into
      a raw JSON value — so the moment a participant's answer contains a double
      quote or a line break, the request body stops being valid JSON and the
      endpoint replies ``400 Invalid JSON format``. Open-ended answers contain
      quotes eventually, so this will bite during a real study rather than
      during your pilot.

      The endpoint accepts whichever encoding your survey tool sends —
      Qualtrics posts ``application/x-www-form-urlencoded``, a script will
      usually send JSON, and both are handled. Form encoding is the safer of
      the two, because Qualtrics escapes every value itself.

      In **Body Parameters**:

      ===================================== ======== ==============================
      Parameter                             Type     Value
      ===================================== ======== ==============================
      ``survey_id``                         String   your study identifier
      ``participant_id``                    String   piped ``ResponseID``
      *the question text, written out*      String   the piped answer
      ===================================== ======== ==============================

      Add one more row per question — as many as your study has. Any
      parameter that is not ``survey_id``, ``participant_id`` or ``answers``
      is read as a question, and its value as the answer. Nothing has to be
      escaped by hand, and ChatbotLab needs no configuration per study: it
      stores whatever questions you send.

      A parameter whose value does not resolve is stored but left out of the
      bot's prompt, so a broken pipe cannot produce a dangling ``Q:`` with no
      answer.

   For a tool that can build nested JSON safely, the ``answers`` field is still
   accepted in either of these shapes:

   .. literalinclude:: qualtrics/web_service_body.json
      :language: json

   This file also lives in the repository at
   ``docs/survey-integration/qualtrics/web_service_body.json``.

Both the question text and the answer are sent, because the bot reads the
question wording as part of the context. Add as many entries as your study
needs — the number of questions and the length of each answer are uncapped.

In Qualtrics' Web Service editor this goes in **Body Parameters** with the
content type set to ``application/json``:

- ``survey_id`` — type *String*
- ``participant_id`` — type *String*, piped from ``ResponseID``
- ``answers`` — type *JSON*, set to the flat object above

.. note::

   ``answers`` is a flat ``{question: answer}`` object because Qualtrics'
   Body Parameters are flat key/value rows, which makes a nested array
   awkward to build. The API also accepts the equivalent list form —
   ``[{"question": ..., "answer": ...}, ...]`` — which is easier for scripts
   and is the shape everything is stored as. Send whichever your tool can
   express; both end up identical in the database.

   Two questions with identical wording would collide in the flat form. Use
   the list form if your study needs that.

.. note::

   ``survey_id`` and ``participant_id`` must match the ``survey_id`` and
   ``participant_id`` you put in the chatbot URL (see ``Embedding ChatbotLab``
   above). That pair is how ChatbotLab finds the answers again.

Sending the same participant's answers twice overwrites the stored copy rather
than creating a duplicate, so retries and survey restarts are safe.

Step 3 — Turn it on for a bot
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

In the admin panel, open the bot and find **Survey Context**:

``Survey context enabled``
   **The switch.** Tick it to give this bot the participant's answers; leave
   it unticked for a control condition. Both bots can otherwise be identical,
   and both still receive the answers — only the ticked one reads them.

   In a factorial design with a bot per cell, this one checkbox is the
   difference between a treatment and a control bot, so check it before
   running participants. The **Survey ctx** column on the Bots list shows the
   state of every bot at a glance.

``Survey context preamble``
   Optional wording introducing the answers to the model, for example
   ``The participant answered these questions before talking to you:``. The
   answers are appended underneath it as ``Q:``/``A:`` pairs.

   Leaving it blank passes the answers with no introduction. It does **not**
   switch the feature off — the checkbox above does that. (Earlier versions
   used a blank preamble as the off switch, which made a preamble left empty
   by accident indistinguishable from a deliberate control condition.)

Placing the answers inside the prompt
   By default the answers go after the bot's prompt. To put them somewhere
   specific, write ``{{RECALLED_SITUATION}}`` in the bot's prompt where they
   belong. The preamble and ``Q:``/``A:`` pairs replace it, and the rest of
   the prompt is left word for word, which matters when the prompt text was
   approved by your IRB. For example::

      The participant previously wrote:

      <participant_context>
      {{RECALLED_SITUATION}}
      </participant_context>

   When there are no answers to show (the checkbox is off, or the
   participant's answers never arrived), the placeholder is removed, so the
   model never sees it.

``Survey context in followup``
   Whether the answers are also included when the bot sends an idle follow-up
   message. On by default. Turn it off if follow-ups should not reference the
   survey.

Step 4 — Verify before running participants
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Before involving Qualtrics at all, confirm the server side works on its own:

   .. code-block:: bash

      python api/smoke_survey_context.py \
          --base-url https://dev.bot.wwbp.org \
          --token "$SURVEY_INGEST_TOKEN" \
          --bot-name my_test_bot

This performs the same three calls a real study makes and asks the bot to
recall one of the answers, so a pass proves the whole chain end to end. Running
it first separates a server problem from a survey-flow problem, which are
otherwise hard to tell apart. It needs a bot that has a preamble set, and a
server not running with ``MOCK_LLM=true``.

Then take the survey yourself, and in the admin panel check:

1. **Survey responses** — a row for your participant ID with the expected
   number of answers.
2. **Conversations** → your conversation → **Metadata** → ``survey context`` —
   the answers the conversation actually used.
3. **Utterances** → any bot message → ``instruction prompt`` — the full prompt
   sent to the model, with your preamble and the ``Q:``/``A:`` pairs in it.

If ``survey context`` is empty but the survey response row exists, the two
``participant_id``/``survey_id`` pairs do not match, or the Web Service ran
after the chatbot block.

.. warning::

   A participant whose answers never arrive is **not** an error — the bot
   simply receives no extra context, which silently makes them behave like a
   control participant. Those conversations have an empty ``survey context``
   column, so check it for nulls before trusting your condition assignment in
   analysis.

How it is recorded
^^^^^^^^^^^^^^^^^^

Four records make a conversation reproducible after the fact:

- ``SurveyResponse.answers`` — what Qualtrics sent, and when
- ``Conversation.survey_context`` — a snapshot of what *this* conversation
  used, copied at conversation start. It is a copy, not a link, so it stays
  accurate even if Qualtrics later sends new answers for that participant.
- ``Conversation.bot_config`` — the bot's full configuration at the time,
  including the preamble, so you can prove which condition was live
- ``Utterance.instruction_prompt`` — the exact prompt behind every single
  message

Data Linking
------------

- Each conversation is stored with a ``conversation_id``.
- The variable ``conversation_id`` (in ChatbotLab) can then be merged on the ``ResponseID`` variable in your Qualtrics data.

Keystrokes
----------

You can monitor your participant's typing activity by recording their keystrokes.

   .. code-block:: javascript

      // Function to update time counters and send keystroke data
      function handlePageExit() {
         var currentTime = new Date();
         
         // Ensure we account for time on page before the event
         if (!document.hidden) {
            window.totalTimeOnPage += (currentTime - window.pageStartTime);
         } else if (window.awayStartTime) {
            window.totalTimeAwayFromPage += (currentTime - window.awayStartTime);
         }
         
         sendKeystrokeData();
      }

      // Attach both unload and pagehide events
      Qualtrics.SurveyEngine.addOnUnload(handlePageExit);
      window.addEventListener("pagehide", handlePageExit, false);

      // Function to send keystroke data to external API using both window and sessionStorage flags to avoid duplicate sends
      function sendKeystrokeData() {
         // Check both window and sessionStorage flags
         if (window._keystrokeDataSent || sessionStorage.getItem("keystrokeDataSent") === "true") {
            console.log("Keystroke data already sent.");
            return;
         }
         // Set both flags so that duplicate calls are ignored
         window._keystrokeDataSent = true;
         sessionStorage.setItem("keystrokeDataSent", "true");
         
         var conversationID = "${e://Field/ResponseID}"; // Embedded data from Qualtrics
         var payload = JSON.stringify({
            conversation_id: conversationID,
            total_time_on_page: window.totalTimeOnPage,
            total_time_away_from_page: window.totalTimeAwayFromPage,
            keystroke_count: window.keystrokeCount
         });
         
         if (navigator.sendBeacon) {
            navigator.sendBeacon("https://bot.wwbp.org/api/update_keystrokes/", payload);
            console.log("Keystroke data sent using sendBeacon.");
         } else {
            fetch("https://bot.wwbp.org/api/update_keystrokes/", {  
                  method: "POST",
                  headers: {
                     "Content-Type": "application/json"
                  },
                  body: payload
            })
            .then(response => response.json())
            .then(data => console.log("Keystroke data successfully sent:", data))
            .catch(error => console.error("Error sending keystroke data:", error));
         }
      }


Validation
----------

1. Preview your Qualtrics survey.
2. Open developer console (F12) → check for "Generated botURL" logs.
3. Confirm the embedded ChatbotLab iframe loads successfully.

Other Options
-------------

- You may also want to add a timer question which ensures the participant stays on the chat window for a specified amount of time. 