"""Features: the things a run can do that are not the run itself.

Step 2 of docs/DESIGN-modular-architecture.md §5. Two roles, deliberately not
one interface:

    gates/      may REFUSE a run   (few)
    detectors/  may REPORT on one  (many)

Refusing and reporting are different powers. A single `Feature` interface would
force every detector to implement an empty veto hook -- a fat interface, and one
that quietly invites a detector to start refusing things.
"""
