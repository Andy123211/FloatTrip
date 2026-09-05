## MODIFIED Requirements

### Requirement: Prompt preserves role and memory precedence
The system SHALL reconstruct each main-Agent turn in this order: fixed system instruction, fixed tool definitions, deterministically serialized frozen memory, conversation summary, recent persisted messages, and the current user message. Dynamic date, Brief, active Run, and bound-itinerary state MUST be obtained through `get_planning_context()` and MUST NOT be inserted before the stable memory prefix.

#### Scenario: Prompt is assembled for a later turn
- **WHEN** the system prepares the next main-Agent invocation
- **THEN** frozen active memory remains a byte-stable prefix for that Conversation and dynamic planning state is not embedded into that prefix

#### Scenario: Summary is refreshed
- **WHEN** long-message compression replaces older messages with a new summary
- **THEN** only prompt content after the frozen memory prefix changes because of that summary update

## ADDED Requirements

### Requirement: Frozen memory can be queried safely
The system SHALL provide a typed memory tool that reads only active facts in the current Conversation snapshot and supports bounded category, destination, companion, polarity, and provenance filters.

#### Scenario: Memory contains candidate facts
- **WHEN** the Agent queries long-term memory
- **THEN** candidate, superseded, rejected, expired, or non-snapshot facts are excluded

#### Scenario: User asks for travel preferences
- **WHEN** active memory contains requirements, avoidances, and preferences
- **THEN** the Agent distinguishes their polarity and does not infer an exaggerated traveler persona from missing evidence

