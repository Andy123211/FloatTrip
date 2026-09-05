## ADDED Requirements

### Requirement: Tool lifecycle is exposed as product activity
The system SHALL map tool execution to `agent.activity.started`, `agent.activity.completed`, and `agent.activity.failed` events with an `activity_id` derived from `tool_call_id`.

#### Scenario: A tool starts and completes
- **WHEN** the ReAct graph emits tool start and finish records
- **THEN** clients receive correlated product activity events in lifecycle order

#### Scenario: A tool fails
- **WHEN** tool execution raises an error
- **THEN** clients receive a sanitized failed event without raw arguments, results, memory text, stack traces, or model reasoning

### Requirement: Tools can emit transient progress
Registered tools SHALL be able to emit `agent.activity.progress` through the runtime stream writer.

#### Scenario: A background Run is being created
- **WHEN** a tool reports intermediate progress
- **THEN** active clients receive a correlated progress event containing only approved stage and display fields

### Requirement: Streaming durability follows recovery needs
The system SHALL persist activity lifecycle events while a Run is active, SHALL keep high-frequency progress and token deltas transient, and SHALL persist the final assistant message and artifacts.

#### Scenario: Client reconnects during an active Chat Run
- **WHEN** the client reloads events after the last durable sequence
- **THEN** it can reconstruct started/completed/failed activity state from persisted events

#### Scenario: Client reconnects after Chat completion
- **WHEN** the Chat Run has completed
- **THEN** the final persisted message and artifacts are sufficient to restore the final UI

### Requirement: Agent metrics are recorded without affecting replies
The system SHALL collect available main-Agent turns, tool-call counts, latency, failure reason, and provider cache hit/miss token metrics.

#### Scenario: Provider omits cache metrics
- **WHEN** a model response does not contain cache-token fields
- **THEN** the Chat Run still completes and metric collection records available values only

