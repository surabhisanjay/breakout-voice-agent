# Recommendation Engine Break Suite

Scope: 50 realistic customer conversations designed to stress recommendation quality only.  
Current expected system response is based on the deterministic production path and recent recommendation-trigger rules.

Legend:
- Routing failure: wrong agent/path or answer order risk.
- Memory failure: prior facts likely ignored, overwritten, or not used.
- Repetition: likely repeated recommendation, repeated qualification, or stale option.
- Score: recommendation quality risk score, 1 = poor, 10 = strong.

## First Timers

| # | Customer Conversation | Current Expected System Response | Human Booking Host Would Say | Routing Failure | Memory Failure | Repetition | Score |
|---|---|---|---|---|---|---|---|
| 1 | C: We are 4 adults, first time. What do you recommend? | If no location known: asks location before naming rooms. | “Which branch are you considering? Once I know that, I’ll suggest the easiest first room there.” | No | No | No | 8 |
| 2 | C: Whitefield. C: We are 4 adults, first time. C: What do you recommend? | Recommends Murder Mystery or Hostage for beginners, may not prioritize Whitefield-specific nuance. | “For Whitefield first-timers, start with Murder Mystery. If you want more pressure, Hostage is the next step.” | No | Possible | No | 7 |
| 3 | C: We are a couple. C: First time. C: What would you choose? | Couple path may recommend Murder Mystery and Hostage. | “For a first-time couple, Murder Mystery is easiest to get into. Hostage is better if you want tension.” | No | No | No | 8 |
| 4 | C: Family of 5, first time, kids aged 10. C: Recommend one. | May recommend Murder Mystery and Hostage, location permitting. | “For kids aged 10, I’d keep it lighter: Murder Mystery first, Hostage only if they like urgency.” | No | Possible | No | 7 |
| 5 | C: We are teens, first time. C: JP Nagar. C: Best room? | Could choose Murder Mystery or Hostage, but JP Nagar inventory includes Missile Attack too. | “For first-time teens at JP Nagar, Murder Mystery is safer. Missile Attack is stronger if they want action.” | No | Possible | No | 7 |
| 6 | C: First-time corporate team, 20 people. C: What room should we play? | Routes to corporate event coordination, not single-room recommendation. | “For 20 people, I wouldn’t force one room. I’d split teams across rooms or coordinate a corporate activity.” | No | No | No | 9 |
| 7 | C: I’ve never played. C: What is your best beginner room? | If no location, asks location first. | “I’ll recommend after branch because rooms vary by location. Which location works for you?” | No | No | No | 8 |
| 8 | C: Koramangala. C: First time adults. C: What do most first-timers choose? | Likely Murder Mystery/Hostage. | “Most first-timers choose Murder Mystery. Hostage is the step-up if they want more adrenaline.” | No | No | Low | 8 |

## Preference Changes

| # | Customer Conversation | Current Expected System Response | Human Booking Host Would Say | Routing Failure | Memory Failure | Repetition | Score |
|---|---|---|---|---|---|---|---|
| 9 | C: Beginner room please. C: Actually we want intermediate. | May retain beginner context unless challenge preference overrides. | “Got it, not too easy. Which location? I’ll avoid the softest beginner picks.” | Possible | Yes | Possible | 5 |
| 10 | C: Mystery room. C: Actually thriller. | May not cleanly switch from story/mystery to thrill preference. | “Sure, switching from mystery to thriller. At which branch?” | Possible | Yes | No | 5 |
| 11 | C: Something intense. C: Actually more story, less pressure. | Recent thrill terms may keep challenge preference active. | “No problem. I’ll move away from pressure and suggest the story-led option for your branch.” | Possible | Yes | Possible | 5 |
| 12 | C: Easy room. C: That’s too easy. Give me a better option. | May detect “better” and recommend challenging option if location known. | “Understood. I’ll step it up one level, not go straight to the hardest room.” | No | Possible | No | 7 |
| 13 | C: Recommend Murder Mystery. C: I don’t like that room. | May still repeat Murder Mystery if beginner context remains. | “Fair. I’ll avoid Murder Mystery. Do you prefer urgency, action, or story?” | Possible | Yes | Yes | 4 |
| 14 | C: Give me another recommendation. C: No, another one. C: No, something else. | May cycle same two rooms or fall to options list. | “Okay, I’ll stop repeating those. Here are three different styles available at this branch.” | Possible | Yes | Yes | 3 |
| 15 | C: I want mystery. C: What else do you have? | Inventory path should list available rooms, may not preserve mystery preference. | “Besides Murder Mystery, Undercover gives more story; Hostage gives urgency.” | No | Possible | No | 6 |
| 16 | C: I don’t want puzzles. C: But I still want challenge. | No-puzzle concern may force Murder Mystery despite challenge request. | “Then avoid logic-heavy rooms; choose a more physical/search-led challenge.” | Possible | Yes | Possible | 4 |

## Location Awareness

| # | Customer Conversation | Current Expected System Response | Human Booking Host Would Say | Routing Failure | Memory Failure | Repetition | Score |
|---|---|---|---|---|---|---|---|
| 17 | C: Whitefield. C: We are 4 adults. C: Recommend something intense. | Should recommend Bomb Defusal or Undercover. | “At Whitefield, Bomb Defusal is the strongest intense pick; Undercover is story-led.” | No | No | No | 8 |
| 18 | C: Koramangala. C: 6 adults. C: Hardest room? | Likely Classified/Undercover. | “At Koramangala, Classified is the sharper challenge.” | No | No | No | 8 |
| 19 | C: JP Nagar. C: 4 adults. C: Something not easy. | Likely Missile Attack or Prison Break wording mismatch risk because inventory says Missile Attack. | “At JP Nagar, I’d suggest Missile Attack for a stronger mission-style challenge.” | Possible | No | No | 6 |
| 20 | C: We are 4 adults. C: Recommend one. | Should ask location first; no branch/room names. | “Which location would you like to visit?” | No | No | No | 9 |
| 21 | C: Recommend a thriller. C: We’ll go Whitefield. | First response should ask location; second should use Whitefield. | “For Whitefield thriller-style, I’d choose Bomb Defusal for pressure or Undercover for story.” | No | Possible | No | 7 |
| 22 | C: Whitefield. C: Actually JP Nagar. C: Best room? | Must switch inventory to JP Nagar. | “Got it, JP Nagar. I’d recommend Murder Mystery for easier story, Missile Attack for action.” | Possible | Yes | No | 5 |
| 23 | C: Koramangala. C: Actually Whitefield. C: Something intense. | Should clear Koramangala options and recommend Whitefield options. | “Switched to Whitefield. For intensity, Bomb Defusal is the better fit.” | Possible | Yes | No | 6 |
| 24 | C: Which branch has the best rooms? | Direct location comparison may answer all branches, but should not overclaim. | “Depends on what you want: Koramangala has the widest mix; Whitefield is strong for intensity; JP Nagar is simpler.” | No | No | No | 8 |

## Recommendation Comparison

| # | Customer Conversation | Current Expected System Response | Human Booking Host Would Say | Routing Failure | Memory Failure | Repetition | Score |
|---|---|---|---|---|---|---|---|
| 25 | C: Murder Mystery or Hostage? | Direct comparison should answer. | “Murder Mystery is calmer and clue-led; Hostage has more urgency.” | No | No | No | 9 |
| 26 | C: Which is better? after discussing Murder Mystery/Hostage. | Contextual best should lean Murder Mystery for first visit. | “For first-timers, Murder Mystery. For adrenaline, Hostage.” | No | Possible | Low | 8 |
| 27 | C: What is your best room? no location. | Should ask location first, no room names. | “Best depends on branch. Which location?” | No | No | No | 9 |
| 28 | C: What sells the most at Whitefield? | Should say Murder Mystery usually most popular at Whitefield. | “Murder Mystery is usually the safest/popular pick at Whitefield.” | No | No | No | 8 |
| 29 | C: What do most first-timers choose? no location. | Should ask location first. | “Usually Murder Mystery, but I’ll confirm after branch. Which location?” | Possible | No | No | 7 |
| 30 | C: What would you personally choose for 4 adults at Whitefield? | Might choose Bomb Defusal due adults/challenge heuristic. | “If first time, Murder Mystery. If you want intensity, Bomb Defusal.” | No | Possible | No | 7 |
| 31 | C: Top 3 rooms at Koramangala? | May only give 1-2 or options list. | “Classified for challenge, Undercover for story, Murder Mystery for easiest entry.” | Possible | No | No | 6 |
| 32 | C: Second-best room at Whitefield? | “Second-best” likely not handled specifically. | “If Murder Mystery is the safest first pick, I’d call Hostage or Undercover the next best depending on thrill vs story.” | Possible | No | No | 5 |

## Thrill Requests

| # | Customer Conversation | Current Expected System Response | Human Booking Host Would Say | Routing Failure | Memory Failure | Repetition | Score |
|---|---|---|---|---|---|---|---|
| 33 | C: Something scary. | Direct answer says not horror/jump-scare; asks/uses location. | “Not horror, but for suspense I can suggest the most intense option at your branch.” | No | No | No | 8 |
| 34 | C: Something intense at Whitefield. | Should suggest Bomb Defusal, Undercover alternative. | “Bomb Defusal. If you want story instead of pressure, Undercover.” | No | No | No | 9 |
| 35 | C: Something not for beginners at Koramangala. | Should suggest Classified/Undercover. | “Classified is the sharper challenge at Koramangala.” | No | No | No | 8 |
| 36 | C: I don’t want an easy room. We’re at JP Nagar. | Should avoid Murder Mystery if seeking challenge. | “Then I’d avoid the easiest path and check Missile Attack/Hostage depending on availability.” | Possible | No | No | 6 |
| 37 | C: Horror room for 2 people? | Should clarify not horror; location needed. | “We don’t have jump-scare horror. Which branch? I’ll suggest the most suspenseful option.” | No | No | No | 8 |
| 38 | C: We want adrenaline, 6 adults, Whitefield. | Should suggest Bomb Defusal if capacity supports. | “Bomb Defusal is the best pressure pick for 6 adults at Whitefield.” | No | No | No | 9 |

## Group Size

| # | Customer Conversation | Current Expected System Response | Human Booking Host Would Say | Routing Failure | Memory Failure | Repetition | Score |
|---|---|---|---|---|---|---|---|
| 39 | C: 2 people, first time, Whitefield. Recommend. | Valid options only 2+ rooms; may recommend Murder Mystery/Hostage. | “For 2 first-timers, Murder Mystery is easiest; Hostage if you want urgency.” | No | No | No | 8 |
| 40 | C: Couple, 2 people, want intense. | Couple intent may bias Murder Mystery even if intense. | “For a couple wanting intensity, I’d avoid the softest room and pick the most active option at your branch.” | Possible | Yes | No | 5 |
| 41 | C: 4 adults, Whitefield, hard room. | Should suggest Bomb Defusal/Undercover. | “Bomb Defusal for pressure, Undercover for story.” | No | No | No | 8 |
| 42 | C: 6 adults, JP Nagar, best room. | Must respect JP Nagar capacity/inventory. | “For 6 at JP Nagar, Murder Mystery or Hostage fit; I’d avoid unsupported rooms.” | No | Possible | No | 7 |
| 43 | C: 10 people, Whitefield, recommend a single room. | Should say single-room capacity issue, coordinate split. | “No single room is right for 10. I’d split across rooms or coordinate with events.” | No | No | No | 9 |
| 44 | C: 10 people, make it 6 actually. Recommend Whitefield. | Must update participant count and stop capacity failure. | “For 6 at Whitefield, Bomb Defusal if intense, Murder Mystery if first-time.” | Possible | Yes | No | 5 |

## Edge Cases

| # | Customer Conversation | Current Expected System Response | Human Booking Host Would Say | Routing Failure | Memory Failure | Repetition | Score |
|---|---|---|---|---|---|---|---|
| 45 | C: I don’t like that. C: Another. C: Another. | May repeat top two or ask preference. | “I’ll stop repeating those. Do you want story, pressure, or difficulty?” | Possible | Yes | Yes | 3 |
| 46 | C: Top 3 hidden gems at Koramangala. | Hidden gems not explicitly modeled; may give general options. | “I wouldn’t call them hidden gems, but Undercover, Classified, and Pharaoh are less obvious than Murder Mystery.” | Possible | No | No | 5 |
| 47 | C: Hardest room in all locations? | May ask location first due guard. | “Hardest depends on branch and group size. Koramangala has Classified; Whitefield has Bomb Defusal.” | Possible | No | No | 6 |
| 48 | C: Easiest room for my parents, Whitefield. | May recommend Murder Mystery/Hostage if age group parsed. | “Murder Mystery is the safest lighter option at Whitefield.” | No | Possible | No | 7 |
| 49 | C: Best room for someone who hates scary things but wants excitement. | Needs nuanced balance; may over-index thrill/no horror. | “Avoid scary pressure. Choose Murder Mystery for safe excitement or Undercover if they want more story.” | Possible | Possible | No | 5 |
| 50 | C: What else do you have besides Murder Mystery at JP Nagar? | Should list JP Nagar options except Murder Mystery. | “At JP Nagar, Hostage and Missile Attack are the alternatives.” | Possible | Possible | Low | 5 |

## Highest-Risk Failure Themes

1. Rejection handling may repeat the same two rooms instead of excluding rejected rooms.
2. Preference changes can leave stale `experience_level` or `challenge_preference` active.
3. “Second-best,” “hidden gem,” and “top 3” are not clearly modeled.
4. JP Nagar recommendations risk mismatch between older room names and current inventory.
5. Couple intent can override thrill/challenge preference.
6. Large-group correction from 10 to smaller group must be watched for stale capacity state.
7. First-time + explicit recommendation is strong; first-time without explicit request correctly asks location first.
8. Location guard is strong, but comparison questions can still pressure the system to name branches.

## Suggested Replay Priority

Run these first because they are most likely to break recommendation quality:

- #13 recommendation rejection
- #14 reject three times
- #22 location change to JP Nagar
- #32 second-best room
- #40 couple plus intense preference
- #44 participant correction 10 to 6
- #46 hidden gems
- #50 “besides Murder Mystery”
