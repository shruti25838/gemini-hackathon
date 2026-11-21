# fixtures/photos

`MOCK_ROOM_INPUT` serves a saved photo from here instead of a live capture.
The team integration doc names two files:

| File | What it should be |
| --- | --- |
| `room_good.jpg` | A clean, well-lit room corner. One wall measurable, a door with a visible hinge side, no person in frame. Passes every Decision 1 photo gate. |
| `room_poor.jpg` | A deliberately bad input: motion blur, a person in frame, or a crop too tight to read the room. Must be rejected by the gates, leaving the last good room intact. |

**Neither is committed yet.** They are real photographs and lane B supplies
them at capture time. They are not generated, because a fabricated placeholder
that looked like a room photo would be exactly the kind of mock that passes
for real, and the fixture set is meant to prove the opposite.

Requirements when they are added:

- EXIF and GPS stripped before committing, the same way the browser strips
  them on upload.
- No identifiable person in either frame.
- Keep them small, roughly 1500 px on the long edge.
- Record their license and source in the README's asset table.
