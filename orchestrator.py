
from agent_state import BOOK_STATE


class AgentOrchestrator:
    def handle(self, action: str, payload: dict | None = None):
        payload = payload or {}

        # --- TECH ---
        if action == "PING":
            return {
                "status": "OK",
                "action": action,
                "text": None,
                "meta": {}
            }

        # --- WRITE CHAPTER ---
        if action == "WRITE_CHAPTER":
            BOOK_STATE["stage"] = "WRITING"
            BOOK_STATE["current_text"] = ""
            BOOK_STATE["last_action"] = "WRITE_CHAPTER"

            prompt = payload.get("prompt", "Napisz rozdział książki.")

            return {
                "status": "OK",
                "action": action,
                "text": prompt,
                "meta": {
                    "stage": BOOK_STATE["stage"]
                }
            }

        # --- CONTINUE CHAPTER ---
        if action == "CONTINUE_CHAPTER":
            BOOK_STATE["stage"] = "CONTINUE"
            BOOK_STATE["last_action"] = "CONTINUE_CHAPTER"

            previous_text = BOOK_STATE.get("current_text", "")

            prompt = f"""
            Kontynuuj poniższy rozdział książki.
            Zachowaj styl i logikę. NIE kończ rozdziału.

            === DOTYCHCZASOWY TEKST ===
            {previous_text}
            """

            return {
                "status": "OK",
                "action": action,
                "text": prompt,
                "meta": {
                    "stage": BOOK_STATE["stage"]
                }
            }

        # --- FALLBACK ---
        return {
            "status": "ERROR",
            "action": action,
            "text": None,
            "meta": {
                "reason": "UNKNOWN_ACTION"
            }
        }
