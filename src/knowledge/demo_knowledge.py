import re

def get_demo_answer(message: str) -> str:
    lowered = message.lower()
    
    # 1. Kids location queries should fall through to the specific kids handler in inbound agent
    if "kids" in lowered or "children" in lowered:
        # If it's a recommendation request or general question about kids games, handle it,
        # but location queries for kids must return "" to let the specific kids location handler run.
        if "location" in lowered or "where" in lowered or "which" in lowered or "better" in lowered:
            return ""

    # 2. Compare rooms
    if "compare" in lowered or "difference between" in lowered or "difference" in lowered:
        if "murder" in lowered or "mystery" in lowered or "hostage" in lowered:
            return "Murder Mystery is a classic detective story with a focus on searching and investigating, making it more relaxed and ideal for beginners. Hostage, on the other hand, is a high-suspense rescue game where you start chained and must escape under intense time pressure."
        if "classified" in lowered or "bomb" in lowered:
            return "Classified is a secret agent espionage room with cryptic puzzle-solving and codebreaking. Bomb Defusal is a fast-paced, high-pressure teamwork challenge focused on defusing a bomb under tight communication."
        if "prison" in lowered or "undercover" in lowered:
            return "Prison Break is a traditional mechanical breakout game where you escape from jail cells. Undercover is an immersive spy thriller focused on narrative, hidden doors, and secret operations."
        return "We offer several rooms: Murder Mystery (detective story) and Hostage (rescue mission) are great for beginners. For a higher difficulty challenge, we have Classified (espionage), Bomb Defusal (high pressure defusal), Prison Break (classic cell breakout), and Undercover (spy thriller)."

    # 3. Explain all rooms
    if any(term in lowered for term in (
        "all rooms", "all games", "what rooms", "what games", "explain rooms",
        "list of rooms", "explain all", "room options", "tell me about the rooms",
        "tell me about rooms", "explain the rooms", "can explain rooms"
    )):
        return (
            "We have six exciting experiences: "
            "1. Murder Mystery: A classic detective investigation solving a crime. "
            "2. Hostage: A high-suspense rescue mission where you start chained. "
            "3. Classified: An espionage thriller in a secret agent's headquarters. "
            "4. Bomb Defusal: An intense, high-pressure defusal mission. "
            "5. Prison Break: A mission to escape from a realistic jail cell. "
            "6. Undercover: A spy adventure full of hidden doors and plot twists."
        )

    # 3a. Duration / how long
    if any(term in lowered for term in (
        "how long", "duration", "how much time", "long is the game", "long does it take",
        "long is the experience", "how many minutes", "time limit"
    )):
        return (
            "Each escape room session is 50 minutes long. We recommend arriving at least 10-15 minutes "
            "early so you have time for the welcome briefing before your game starts."
        )

    # 3b. Rules & how it works
    if any(term in lowered for term in (
        "explain rules", "explain the rules", "what are the rules", "how does it work",
        "how does the escape room work", "what happens inside",
        "first time here"
    )):
        return (
            "Breakout Escape Rooms are immersive, real-life adventure games where you and your team are locked in a themed room and have 50 minutes to solve puzzles, find clues, and complete a mission to escape. Our team briefs you beforehand, monitors the game, and can assist if needed."
        )

    if "food" in lowered or "menu" in lowered:
        return (
            "Food options include continental food, build-your-menu options, mix snack boxes, "
            "hi-tea options, and Indian buffet options for corporate events."
        )

    # 4. Explain specific rooms
    if "murder" in lowered or "mystery" in lowered:
        return "Murder Mystery is an investigation-style escape room with clues, puzzles, and a story-driven mystery. It is a classic, immersive detective investigation where you find clues and solve a crime. It is beginner-friendly and great for a wide range of ages. It is offered at Koramangala, Whitefield, and JP Nagar."
    if "hostage" in lowered:
        return "Hostage is a rescue-style escape room with urgency, teamwork, and investigation elements. You are chained inside a dark room and must coordinate closely with your team to break free and escape under the pressure of a ticking clock. It is offered at Koramangala, Whitefield, and JP Nagar."
    if "classified" in lowered:
        return "Classified is a military-style, tense espionage room. Your team enters a secret agent's headquarters to recover critical intelligence. It features cryptic puzzles and highly mission-focused gameplay. It is offered at Koramangala."
    if "bomb" in lowered or "defusal" in lowered:
        return "Bomb Defusal is an intense, high-stakes game where your team is tasked with defusing a simulated bomb. It requires rapid communication, clear head under pressure, and precise teamwork. It is offered at Whitefield."
    if "prison" in lowered or "jail" in lowered or (
        "breakout" in lowered
        and any(kw in lowered for kw in ("room", "game", "prison break", "break out", "cell", "lock"))
        and not re.search(r"^(hi|hey|hello|is this|this is|yes|no|breakout\?)\b", lowered.strip())
    ):
        return "Prison Break is a classic escape room where you start locked in a cell. The focus is on finding hidden escape routes, solving mechanical challenges, and working together to break out. It is offered at JP Nagar."
    if "undercover" in lowered:
        return "Undercover is a spy-themed thriller room filled with secrets, hidden passageways, and unexpected plot twists, perfect for teams looking for an immersive narrative. It is offered at Koramangala and Whitefield."

    # 5. What happens if we fail / don't escape (combining original test substrings)
    if any(term in lowered for term in ("fail", "don't escape", "do not escape", "can't escape", "cannot escape", "what happens if we don't", "what happens if we fail")):
        return (
            "Don't worry, we won't keep you locked forever. Players are not actually locked in, and our team "
            "monitors the game and can assist when needed. If time runs out, our game master will let you out and explain the remaining puzzles."
        )

    # 6. Late arrival / running late (exact match to original test substrings)
    if any(term in lowered for term in ("late", "delay", "behind time", "traffic")):
        has_duration = bool(re.search(r"\d+", lowered)) or "min" in lowered
        if has_duration:
            return (
                "Don't worry, let me help. The game starts at the scheduled time because sessions run back to back, "
                "so arriving late reduces the time available inside the room."
            )
        return (
            "Don't worry, let me help. The game starts at the scheduled time because sessions run back to back, "
            "so arriving late reduces the time available inside the room. How late do you expect to be?"
        )

    # 7. Parking / location questions (exact match to original test substrings)
    if "parking" in lowered or "park" in lowered:
        return (
            "Koramangala has basement and street parking, Whitefield has basement car parking with dedicated spots and general parking, "
            "and JP Nagar has street parking."
        )
    if "where are you" in lowered or "our locations" in lowered or "locations" in lowered or (
        "location" in lowered and any(w in lowered for w in ("which", "what", "better", "are", "have", "address", "list", "know", "where"))
    ):
        # If this is a compound question (also asking about food, parking, etc.)
        # let _answer_multiple_questions handle the full response.
        is_compound = any(t in lowered for t in ("food", "parking", "park", "how long", "duration", "cancel", "refund"))
        if not is_compound:
            return "Breakout has locations in Koramangala, Whitefield, and JP Nagar."

    return ""
