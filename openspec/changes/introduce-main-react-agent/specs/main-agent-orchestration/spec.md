## ADDED Requirements

### Requirement: Chat Run supports a tool-using main Agent
The system SHALL provide a LangChain `create_agent` Chat execution mode that can stream assistant tokens, invoke only the registered travel tools, and finish with one persisted assistant message.

#### Scenario: Ordinary conversation needs no tool
- **WHEN** the user sends a message that can be answered from the supplied conversation context
- **THEN** the main Agent streams and persists a response without invoking an unrelated tool

#### Scenario: Tool use continues the same Chat Run
- **WHEN** the main Agent invokes a registered tool
- **THEN** the tool result is returned to the same ReAct loop and the final assistant response remains associated with the original Chat Run

### Requirement: Runtime identity is injected and not model-controlled
The system MUST inject `user_id`, `conversation_id`, `chat_run_id`, and frozen memory revision through runtime context and MUST NOT expose those fields as model-fillable tool arguments.

#### Scenario: Model supplies a forged identity
- **WHEN** a tool invocation includes an unrecognized identity or scope argument
- **THEN** schema validation rejects the invocation without reading or changing another user's data

#### Scenario: Tool reads scoped data
- **WHEN** a registered tool accesses memory, messages, briefs, runs, or itineraries
- **THEN** it derives the owner and conversation scope exclusively from runtime context

### Requirement: Database remains the cross-turn source of truth
The system SHALL rebuild each turn from persisted Conversation data and SHALL use a Chat Run-specific checkpoint only for the current ReAct loop.

#### Scenario: A later turn starts
- **WHEN** a user submits another message in the same Conversation
- **THEN** the system loads messages, summary, and frozen memory from the database instead of resuming a previous Chat Run checkpoint

#### Scenario: A Chat Run is retried
- **WHEN** the current Chat Run resumes or retries
- **THEN** it uses that Run's checkpoint without mutating another Run's checkpoint state

### Requirement: Planning and revision are delegated to background Runs
The system SHALL create independent `TRAVEL_PLAN` and `REVISION` Runs for formal planning and itinerary modification and SHALL return control to the Chat Run without awaiting their completion.

#### Scenario: Ready brief is explicitly submitted
- **WHEN** the current Brief is ready and the user explicitly requests formal planning
- **THEN** `submit_current_brief` creates or returns exactly one Planning Run and the Chat Run may complete immediately

#### Scenario: User explicitly requests a revision
- **WHEN** the user identifies an owned modifiable itinerary and gives modification notes
- **THEN** `start_revision` creates an independent Revision Run and the Chat Run may complete immediately

### Requirement: Main Agent tools provide bounded travel retrieval
The system SHALL expose bounded tools for frozen memory, current planning context, saved-itinerary search, and saved-itinerary detail.

#### Scenario: Search finds itinerary revisions
- **WHEN** saved itineraries share a `root_id` and `include_revisions` is false
- **THEN** the search returns only the highest version for that root and at most five results

#### Scenario: Itinerary detail has no day
- **WHEN** the Agent requests a saved itinerary without specifying a day
- **THEN** the tool returns a bounded summary and does not inject the full plan into model context

#### Scenario: Visited destinations are requested
- **WHEN** the user asks where they have been
- **THEN** the Agent uses only active `destination_history` memory as visit evidence and does not treat saved plans or candidate facts as completed travel

### Requirement: Agent mode is reversibly configurable
The system SHALL support `CHAT_AGENT_MODE=decision|react` and SHALL default to `decision` during the migration period.

#### Scenario: React mode is disabled
- **WHEN** `CHAT_AGENT_MODE` is unset or set to `decision`
- **THEN** existing DialogueDecision behavior remains available without requiring data rollback

