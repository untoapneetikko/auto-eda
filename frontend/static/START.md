# Start the Schematic Designer

## First time setup

Open a terminal in this folder and run:
```
npm install
npm start
```

Then open: http://localhost:3030

---

## How to parse a datasheet

1. Open http://localhost:3030
2. Drop a component PDF onto the upload zone
3. The server extracts the text automatically
4. A prompt appears on screen — copy it
5. Paste it into Claude Code (this session)
6. Claude Code reads the raw text file and writes the full component profile
7. The browser updates automatically — no refresh needed

---

## Parse command format

When you see a pending component, paste this into Claude Code:

```
parse datasheet [SLUG]
```

Claude Code will:
1. Read `datasheets/[SLUG]/raw_text.txt`
2. Extract all pins, ratings, passives, warnings
3. Write the complete profile to `datasheets/[SLUG]/profile.json`
4. The dashboard updates live

---

## What Claude Code does when parsing

- Reads the full extracted text
- Extracts: part number, description, every pin with type/requirements, absolute max ratings, required passives, common mistakes
- Marks ambiguous pins clearly
- Assigns confidence score (HIGH/MEDIUM/LOW)
- Never guesses — marks uncertain data as ambiguous
- Saves everything to profile.json
