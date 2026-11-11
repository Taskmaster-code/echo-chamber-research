# Stance codebook: YouTube titles (census Day 2)

**For:** both coders, who work independently. **Version:** 11 Nov 2026. Commit this file to Git with the pre-registration.

## The task

Each row of your sheet is one YouTube video title. Rows are shuffled across channels and channel names are hidden. Give each title **one** code for its stance toward the **central government**: the Union government, the Prime Minister and Union ministers, and the party leading the central government, at national level.

| Code | Meaning | Use when the title… |
|---|---|---|
| **S** | Supportive | praises, defends or frames positively the central government, its leaders or its policies; **or** attacks its main national opponents (opposition parties or leaders) |
| **C** | Critical | attacks, mocks or frames negatively the central government, its leaders or its policies; **or** defends or praises the opposition against it |
| **N** | No stance | is factual or neutral; is about something else (a state matter with no central-government angle, crime, weather, sport, international news without a government angle); or you can't tell |

## Rules

1. **Code the title only.** Don't open the video, look up the channel, or use what you know about who made it.
2. **Code the framing, not the topic.** A neutral report on a government policy is N. A title that tells you what to think about the policy is S or C.
3. **Question titles** ("Is the scheme failing?") are coded by their obvious lean. If there's no obvious lean → N.
4. **Sarcasm counts** only when it's unmistakable.
5. **When in doubt → N.** Don't guess a stance.
6. **Hindi and Hinglish titles** are coded in the original language. Don't translate them through a tool.
7. **Don't discuss** titles with the other coder until both sheets are submitted.
8. Aim for about 10 seconds a title. Use the `note` column only for genuinely hard cases.

## Invented examples (not real titles)

- *"How the new rural highway scheme changed village life"* → **S**
- *"Opposition's fake claims on inflation EXPOSED"* → **S**
- *"The jobs data the government doesn't want you to see"* → **C**
- *"Why the PM's silence on this question matters"* → **C**
- *"Cabinet approves revised fertiliser subsidy: key points"* → **N**
- *"Heavy rain alert for five states this week"* → **N**

## From titles to a channel label (done afterwards, by script)

Among a channel's titles coded S or C: if at least 2/3 are S → **supportive**; if at least 2/3 are C → **critical**; otherwise, or if fewer than 3 titles carry a stance → **mixed**. Each coder's channel labels are derived separately. Title-level Cohen's κ and channel-level agreement are reported. Disagreements on a channel label go to the arbiter named in the pre-registration.
