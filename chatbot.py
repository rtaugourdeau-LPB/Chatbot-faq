"""
Gemini — client robuste avec retry/fallback.
Conçu pour être étendu en chatbot + FAQ JSON.
"""
import json
import os
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from google import genai
from google.genai import errors
from google.genai import types as gtypes

load_dotenv()

# ── Config ──────────────────────────────────────────────────────────────────
API_KEY = "AIzaSyDOEWISLeya_xPm7KTWEdxbAjpjYWYoNug"

MODELS = [
    "gemini-2.5-flash",       # principal — le plus capable
    "gemini-2.0-flash",       # fallback standard
    "gemini-2.0-flash-lite",  # fallback léger
    "gemini-1.5-flash",       # dernier recours stable
]

MAX_RETRIES = 4
BASE_DELAY  = 1.0
JITTER      = 0.5
RETRY_CODES = frozenset({429, 500, 502, 503, 504})

# Prompt système par défaut — sera enrichi avec la FAQ au moment voulu
DEFAULT_SYSTEM = (
    "Tu es un assistant expert en crowdfunding immobilier français. "
    "Réponds de façon concise, précise et sourcée quand c'est possible."
)


# ── Types ────────────────────────────────────────────────────────────────────
@dataclass(slots=True)
class Turn:
    """Un échange (question + réponse) dans l'historique conversationnel."""
    role:    str   # "user" | "model"
    content: str


@dataclass
class ChatSession:
    """
    Session de chat stateful.
    Conserve l'historique pour les appels multi-tours.
    Peut charger une FAQ JSON pour enrichir le system prompt.
    """
    system_prompt: str = DEFAULT_SYSTEM
    history:       list[Turn] = field(default_factory=list)
    faq:           dict[str, Any] = field(default_factory=dict)

    def load_faq(self, path: str | Path) -> None:
        """Charge la FAQ depuis un fichier JSON et l'injecte dans le system prompt."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        self.faq = data

        # Sérialisation compacte dans le system prompt
        faq_block = json.dumps(data, ensure_ascii=False, indent=2)
        self.system_prompt = (
            f"{DEFAULT_SYSTEM}\n\n"
            f"## FAQ de référence\n"
            f"Utilise les données suivantes pour répondre en priorité :\n"
            f"```json\n{faq_block}\n```"
        )
        print(f"✅ FAQ chargée : {len(data)} entrées")

    def _build_contents(self, user_message: str) -> list[gtypes.Content]:
        """Construit la liste de contenus (historique + nouveau message)."""
        contents = []
        for turn in self.history:
            contents.append(
                gtypes.Content(
                    role=turn.role,
                    parts=[gtypes.Part(text=turn.content)],
                )
            )
        contents.append(
            gtypes.Content(
                role="user",
                parts=[gtypes.Part(text=user_message)],
            )
        )
        return contents

    def add_turn(self, role: str, content: str) -> None:
        self.history.append(Turn(role=role, content=content))

    def clear_history(self) -> None:
        self.history.clear()
        print("🗑️  Historique effacé.")


@dataclass(slots=True)
class Result:
    model:      str
    text:       str
    prompt_tok: int
    answer_tok: int
    total_tok:  int


# ── Client Gemini ────────────────────────────────────────────────────────────
def _error_code(exc: Exception) -> int | None:
    for attr in ("code", "status_code"):
        v = getattr(exc, attr, None)
        if v is not None:
            return int(v)
    return None


def _call_once(
    client: genai.Client,
    model: str,
    contents: list[gtypes.Content],
    system: str,
):
    """Appel unique avec retry exponentiel + jitter."""
    config = gtypes.GenerateContentConfig(system_instruction=system)

    for attempt in range(MAX_RETRIES):
        try:
            return client.models.generate_content(
                model=model,
                contents=contents,
                config=config,
            )
        except (errors.ServerError, errors.ClientError) as exc:
            code = _error_code(exc)
            if code in RETRY_CODES and attempt < MAX_RETRIES - 1:
                delay = BASE_DELAY * (2 ** attempt) + random.uniform(0, JITTER)
                print(
                    f"   ⚠️  HTTP {code} [{model}] "
                    f"— retry dans {delay:.1f}s ({attempt + 1}/{MAX_RETRIES})"
                )
                time.sleep(delay)
            else:
                raise


def send(
    client: genai.Client,
    session: ChatSession,
    user_message: str,
) -> Result:
    """
    Envoie un message, met à jour l'historique, retourne un Result typé.
    Bascule automatiquement sur le modèle suivant si le principal échoue.
    """
    contents  = session._build_contents(user_message)
    last_exc: Exception | None = None

    for model in MODELS:
        print(f"→ {model}")
        try:
            resp  = _call_once(client, model, contents, session.system_prompt)
            usage = resp.usage_metadata
            answer = resp.text

            # Mise à jour de l'historique uniquement en cas de succès
            session.add_turn("user",  user_message)
            session.add_turn("model", answer)

            return Result(
                model      = model,
                text       = answer,
                prompt_tok = usage.prompt_token_count,
                answer_tok = usage.candidates_token_count,
                total_tok  = usage.total_token_count,
            )
        except Exception as exc:
            last_exc = exc
            print(f"   ❌ {model} indisponible ({type(exc).__name__}: {exc})")

    raise RuntimeError(f"Tous les modèles ont échoué — dernière erreur : {last_exc}")


# ── Demo CLI ─────────────────────────────────────────────────────────────────
def main() -> None:
    client  = genai.Client(api_key=API_KEY)
    session = ChatSession()

    # Décommenter quand le fichier FAQ existera :
    # session.load_faq("faq.json")

    questions = [
        (
            "En 3 phrases claires : quelle est la fiscalité applicable aux gains "
            "issus du crowdfunding immobilier pour un particulier résidant en France en 2026 ?"
        ),
        # Exemple de question de suivi (teste le multi-tour) :
        # "Et si les gains dépassent 10 000 € ?",
    ]

    sep = "─" * 70
    for q in questions:
        print(f"\n{sep}\n❓  {q}\n{sep}")
        r = send(client, session, q)
        print(f"\n🤖  [{r.model}]\n\n{r.text}")
        print(f"\n📊  {r.prompt_tok} prompt | {r.answer_tok} réponse | {r.total_tok} total")

    print(f"\n{sep}\n✅  Fin — {len(session.history) // 2} échange(s) dans l'historique.")


if __name__ == "__main__":
    main()
