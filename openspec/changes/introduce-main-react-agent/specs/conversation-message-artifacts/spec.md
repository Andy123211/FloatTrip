## ADDED Requirements

### Requirement: Conversation messages persist typed artifacts
The system SHALL persist an `artifacts` array with each message, return an empty array for legacy messages, and limit each message to five artifacts.

#### Scenario: Assistant response contains cards
- **WHEN** the main Agent completes with itinerary artifacts
- **THEN** the assistant message stores its text and validated artifacts together and the completion event contains the same artifacts

#### Scenario: Legacy message is loaded
- **WHEN** a stored message predates the artifacts column or has no attachments
- **THEN** the conversation API returns `artifacts: []`

### Requirement: Itinerary collections contain bounded card metadata
An `itinerary_collection` artifact MUST contain no more than five cards and MUST NOT contain full `plan_json` content.

#### Scenario: Search returns saved plans
- **WHEN** `find_saved_itineraries` returns exact or near matches
- **THEN** each card contains only itinerary ID, root/version, destination, duration, dates, creation time, modification marker, and a bounded list of highlights

#### Scenario: More than five plans match
- **WHEN** more than five owned plans satisfy the search
- **THEN** the artifact contains at most five cards and reports bounded result metadata

### Requirement: Web clients restore and open itinerary cards
The Web client SHALL render persisted itinerary cards under their assistant message and SHALL open a selected card through the existing itinerary detail endpoint.

#### Scenario: Conversation is refreshed
- **WHEN** a conversation containing an itinerary collection is loaded again
- **THEN** the cards reappear under the original assistant response without replaying transient SSE events

#### Scenario: User activates a card
- **WHEN** the user clicks a card or activates it with the keyboard
- **THEN** the client requests `/api/history/{itinerary_id}` and opens the corresponding complete itinerary

### Requirement: Mobile clients remain protocol-compatible
Mobile API types SHALL accept message artifacts while mobile presentation MAY ignore unsupported artifact types.

#### Scenario: Mobile receives an artifact-bearing message
- **WHEN** the API returns a message with itinerary artifacts
- **THEN** decoding succeeds and the existing message UI remains usable

