# Contributing an evaluation case

A case is one real room, measured with a tape, plus the placements you want
tested in it. It is the only honest way to know whether Ethyneon reads a room
correctly, because the thing being measured is the gap between what the model
infers from a photograph and what the room actually is.

**This repository ships no cases.** `eval/data/cases.json` is not committed and
is ignored by git, because a room photograph belongs to whoever took it. The
harness prints a report of zeroes until somebody writes real ones. We did not
fabricate any, since a fabricated case makes every number in the report a lie.

## Why we ask who wrote each case

Every case carries an `author` and a `group`, and the report counts the two
groups separately.

A pipeline evaluated only on its authors' own rooms looks better than it is.
You photograph the room the way you already know works, you stand where the
light is good, and you measure the wall you were thinking of when you wrote the
prompt. Cases written by people outside the team are the ones that find real
failures. Reporting them together would hide exactly the result worth knowing,
so the harness refuses a case that cannot be placed in one group or the other.

- `group: "team"` means somebody who worked on Ethyneon wrote it.
- `group: "outside"` means anyone else.

## What we need from you

1. **Photographs of one room.** Two to five of them, from the same standing
   position, covering the walls. Ordinary phone photos are right. Do not tidy
   up for the camera.
2. **A tape measurement.** The width and the depth of the room in metres, to
   the nearest centimetre. Height if you can reach. Measure wall to wall at
   floor level, not across furniture.
3. **A note about the door.** Which wall it is on, how far along that wall it
   starts, how wide it is, which side the hinges are on, and whether it opens
   into the room or out of it. This is the single most useful thing in the
   whole case, because the door swing is where fit checks actually fail.
4. **Consent, in writing.** Required for every outside case. See below.

### Measuring the door offset

`offset_m` is measured along the wall, from the wall's **start**, in the
direction the engine traverses it. Walls run clockwise:

    wall_n   (0, 0) -> (W, 0)
    wall_e   (W, 0) -> (W, D)
    wall_s   (W, D) -> (0, D)
    wall_w   (0, D) -> (0, 0)

So on `wall_n` you measure from the left corner as you face that wall from
inside the room, and on `wall_s` you measure from the other end. If that is
confusing, write down which corner you measured from in the `note` field and we
will convert it. A case with the offset measured from the wrong corner is worse
than no case, because it looks valid.

### Consent

Nobody's room goes in this repository without them saying so. For an outside
case we need:

- Confirmation that you took the photographs, or have the right to share them.
- Confirmation that they can sit in a **public** repository indefinitely.
- No identifiable person in any frame.
- No address, no post, no screen showing anything personal, nothing that
  identifies where the room is.

Strip EXIF and GPS before sending. On most phones, sharing a photo through
"save a copy" rather than the original file does this. We will check anyway.

You can withdraw consent at any time and we will remove the case.

## The shape of a case

`eval/data/cases.example.json` holds one annotated example. Copy it to
`eval/data/cases.json` and replace it. Required fields are `id`, `author`,
`group`, `photo` and `truth`; `truth.width_m` and `truth.depth_m` must be
numbers. An outside case also needs `consent.granted`.

```json
{
  "cases": [
    {
      "id": "short-slug",
      "author": "your name or handle",
      "group": "outside",
      "consent": {
        "granted": true,
        "date": "2026-11-23",
        "note": "photos are mine, happy for them to be public, no people in frame"
      },
      "photo": "eval/data/short-slug-1.jpg",
      "truth": { "width_m": 3.2, "depth_m": 2.9, "height_m": 2.45 },
      "door": {
        "wall_id": "wall_n",
        "offset_m": 2.3,
        "width_m": 0.81,
        "hinge_side": "left",
        "swing_direction": "inward",
        "note": "measured from the corner on my left facing the door wall"
      },
      "probes": [
        { "object_id": "desk", "width_m": 1.4, "depth_m": 0.7, "x_m": 2.3, "y_m": 0.35 },
        { "object_id": "desk-clear", "width_m": 1.4, "depth_m": 0.7, "x_m": 0.8, "y_m": 0.35 }
      ],
      "labels": { "is_room": true, "person_visible": false }
    }
  ]
}
```

Put a couple of probes inside the door swing and a couple clear of it. The
interesting number is whether the pipeline still gets the verdict right on the
room it inferred, and you only see that if some probes genuinely do not fit.

## Running it

```bash
# Offline, stub model, no key and no network. Works with no cases.
.venv/Scripts/python -m eval.run_eval --offline

# Against whatever lane A is configured.
.venv/Scripts/python -m eval.run_eval eval/data/cases.json

# Outside cases only.
.venv/Scripts/python -m eval.run_eval eval/data/cases.json --group outside
```

## Reading the report

Per group it prints the authors, how often the photo gate agreed with the
labels, the median error between the drafted room and the tape measure, and how
many fit verdicts computed on the drafted room match the verdicts computed on
the measured room.

Then it prints **false fits** on their own line, and never folds them into an
accuracy score. A false fit is the pipeline saying a desk fits when the measured
room says it hits the door. It is the one error that sends somebody to checkout
for furniture they will have to send back, so it is counted separately and a
single one matters more than a percentage.
