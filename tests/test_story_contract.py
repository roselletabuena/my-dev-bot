"""Reading a story ticket's promises (scope, acceptance scenarios, Definition of Done) without an engine."""

from __future__ import annotations

from conftest import make_issue

from dev_bot.story import parse_story, paths_overlap, shared_paths

# Real story text (VS-212) as the Jira client flattens it: emoji headers, one item per line.
POSITIONER = """📖 User Story
As a pageant organizer,
I want to drag or nudge an uploaded banner or candidate photo to adjust its focal position while seeing a live display preview,
so that I can verify exactly how the cropped image will look before saving.
🔍 Context & Problem Statement
Organizers currently have no way to reposition uploaded images.
🛠️ Technical Scope & Architecture
Layer: Frontend UI Component ("use client")
Target Files / Directories:
src/features/media/components/ImagePositionEditor.tsx
src/features/media/types/index.ts
Architectural Constraints (Design System v1 & Accessibility):
Near-square geometry (rounded-none preview frame, rounded-md 2px controls, 1px border-border / border-foreground).
Live aspect-ratio frame presets: aspect-16/9 (Banner) and aspect-portrait (aspect-4/5, Candidate).
🔗 Issue Links & Relationships
Relationship
Linked Ticket
Blocked By
BE: 1.1 (VS-209)
Depends on persisted focal-point fields (positionX, positionY) and Zod schema contract.
✅ Acceptance Criteria (Gherkin Scenarios)
Scenario 1: Happy Path (Drag to Reposition & Live Preview)
Given an organizer has uploaded a portrait photo in the 4:5 candidate frame
When the organizer drags the image vertically inside the preview frame
Then object-position updates smoothly in real time
Scenario 2: Validation & Error Handling (Boundary Clamping)
Given the organizer drags or nudges past the top, bottom, left, or right edge of the frame
When the pointer or keyboard event exceeds 0% or 100%
Then the focal coordinates clamp strictly between 0 and 100
Scenario 3: Edge Case / Mobile Touch & Keyboard Accessibility
Given an organizer is on a mobile viewport (375px) or navigating via keyboard only
When they touch-drag the preview or focus the positioner and press Arrow keys
And the page does not scroll
Then the focal point updates and is announced to screen readers
🛡️ Quality Gate & Definition of Done
[ ] Strict TypeScript 5 (no any, no non-null assertions !)
[ ] Vitest unit/component tests pass (npm run test:unit)
[ ] WCAG 2.1 AA contrast & keyboard navigation verified
[ ] Passes typecheck (npm run typecheck) and linter (npm run lint)
"""

# VS-209: paths followed by notes in parentheses, including a directory.
SCHEMA = """🛠️ Technical Scope & Architecture
Layer: Prisma Schema & Zod Validation
Target Files / Directories:
prisma/schema.prisma (add bannerPositionX Int @default(50) on Event)
src/lib/validations/ (Zod schemas constraining coordinates to integers 0..100)
docs/schema-summary.md (auto-updated via npm run schema:summary)
Architectural Constraints:
Must use npx prisma migrate dev (no raw SQL).
✅ Acceptance Criteria (Gherkin Scenarios)
Scenario 1: Happy Path
Given a valid payload
When it is persisted
Then the database stores it
"""

# VS-214: descriptive targets only; the Layer line names the directories.
INTEGRATION = """🛠️ Technical Scope & Architecture
Layer: Frontend Feature Integration (src/features/events/, src/features/contestants/ or src/features/voting/)
Target Files / Directories:
Event banner upload & settings form components
Candidate/Contestant creation & edit modal/form components
Architectural Constraints (Design System v1 & Constitution):
Use next/image everywhere (never <img>).
"""


def test_a_story_ticket_yields_its_scope_scenarios_and_definition_of_done():
    story = parse_story(make_issue("VS-212", "FE: 1.1 Interactive Image Positioner", "VS", POSITIONER, labels=["frontend"]))

    assert story.layer == 'Frontend UI Component ("use client")'
    assert story.side == "frontend"
    assert story.target_paths == ["src/features/media/components/ImagePositionEditor.tsx", "src/features/media/types/index.ts"]
    assert story.target_notes == []
    assert [(s.number, s.title) for s in story.scenarios] == [
        (1, "Happy Path (Drag to Reposition & Live Preview)"),
        (2, "Validation & Error Handling (Boundary Clamping)"),
        (3, "Edge Case / Mobile Touch & Keyboard Accessibility"),
    ]
    assert story.scenarios[2].steps[2] == "And the page does not scroll"
    assert len(story.scenarios[0].steps) == 3
    assert story.dod[0] == "Strict TypeScript 5 (no any, no non-null assertions !)"
    assert len(story.dod) == 4
    assert story.ac_tag(story.scenarios[1]) == "AC: VS-212 S2"


def test_constraint_lines_and_link_rows_are_not_mistaken_for_targets_or_steps():
    story = parse_story(make_issue("VS-212", "FE: 1.1", "VS", POSITIONER))
    assert not any("aspect-16/9" in p or "border" in p for p in story.target_paths)
    assert all(step.split()[0] in ("Given", "When", "Then", "And") for s in story.scenarios for step in s.steps)


def test_target_lines_with_notes_keep_only_the_path():
    story = parse_story(make_issue("VS-209", "BE: 1.1 Prisma Focal-Point", "VS", SCHEMA))
    assert story.target_paths == ["prisma/schema.prisma", "src/lib/validations/", "docs/schema-summary.md"]
    assert story.side == "backend"  # from the "BE:" summary prefix
    assert story.dod == []  # no Definition of Done section is fine


def test_descriptive_targets_fall_back_to_paths_named_in_the_layer_line():
    story = parse_story(make_issue("VS-214", "Upload integration", "VS", INTEGRATION))
    assert story.target_paths == ["src/features/events/", "src/features/contestants/", "src/features/voting/"]
    assert story.target_notes == [
        "Event banner upload & settings form components",
        "Candidate/Contestant creation & edit modal/form components",  # a slash, but not a path
    ]
    assert story.side == "frontend"  # from the Layer text


def test_an_unstructured_ticket_yields_an_empty_contract():
    story = parse_story(make_issue("ELE-1", "Fix greeting", "ELE", "Say hello electa"))
    assert (story.target_paths, story.scenarios, story.dod, story.side) == ([], [], [], "")


def test_shared_paths_match_a_file_inside_a_named_directory():
    assert paths_overlap("src/lib/validations/", "src/lib/validations/media.ts")
    assert not paths_overlap("src/lib/validations", "src/lib/validations-old/x.ts")
    a = parse_story(make_issue("VS-209", "BE: a", "VS", SCHEMA))
    b = parse_story(make_issue("VS-216", "BE: b", "VS", SCHEMA.replace("docs/schema-summary.md", "src/lib/supabase/storage-service.ts")))
    assert shared_paths(a, b) == ["prisma/schema.prisma", "src/lib/validations/"]
