## MODIFIED Requirements

### Requirement: Planning intent does not immediately start formal planning
When a user expresses initial planning intent, the main Agent SHALL use the current planning context and controlled Brief tools to continue the conversation instead of immediately starting a formal planning Run.

#### Scenario: User provides only partial trip information
- **WHEN** the user says they want to visit a destination but has not supplied all required planning fields
- **THEN** the system updates the same active Brief with supported facts and asks only for the missing information

#### Scenario: User is only discussing possibilities
- **WHEN** the user explores a destination without explicitly requesting formal planning
- **THEN** the system does not call `submit_current_brief`

### Requirement: Structured planning brief
The system SHALL maintain one structured active Brief per Conversation, and `update_current_brief` SHALL reuse deterministic date, constraint, memory-exclusion, and readiness validation before publishing an authoritative Brief event.

#### Scenario: User refines an existing requirement
- **WHEN** the user changes a previously supplied destination, date, party, budget, or preference
- **THEN** the active Brief is patched rather than replaced by a second competing Brief

#### Scenario: Patch fails deterministic validation
- **WHEN** a proposed Brief patch contains invalid dates or unsupported values
- **THEN** the Brief remains unchanged and the tool returns a bounded validation result the Agent can explain

### Requirement: Explicit formal-planning confirmation
The system SHALL start formal planning only when the current Brief is ready and the current user request explicitly asks to generate or submit the plan. Repeated submission of the same Brief snapshot MUST return the existing Run.

#### Scenario: Ready brief is explicitly confirmed
- **WHEN** all required fields are ready and the user clearly requests generation
- **THEN** `submit_current_brief` creates one immutable Planning Run snapshot

#### Scenario: Confirmation is inferred but not stated
- **WHEN** the model believes the user probably wants a plan but the current request is not explicit
- **THEN** the tool refuses submission and the Agent asks for confirmation

