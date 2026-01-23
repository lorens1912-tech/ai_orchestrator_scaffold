import os
from llm_client import generate_text as _generate_text

# Domyślne modele (możesz nadpisać ENV):
#   setx OPENAI_MODEL_WRITE "gpt-4.1-mini"
#   setx OPENAI_MODEL_OUTLINE "gpt-4.1-mini"
DEFAULT_WRITE_MODEL = os.getenv("OPENAI_MODEL_WRITE") or os.getenv("MODEL_WRITE") or "gpt-4.1"
DEFAULT_OUTLINE_MODEL = os.getenv("OPENAI_MODEL_OUTLINE") or os.getenv("MODEL_OUTLINE") or "o3"


def gpt5(prompt: str, **kwargs) -> str:
    """
    Writer model wrapper.
    Pipeline może wołać gpt5(...) – tu nie ma STUB, jest realne API.
    """
    model = kwargs.pop("model", DEFAULT_WRITE_MODEL)
    return _generate_text(prompt, model=model, **kwargs)



def gpt4(prompt: str, **kwargs) -> str:
    """
    Outline / logic model wrapper.
    """
    model = kwargs.pop("model", DEFAULT_OUTLINE_MODEL)
    return _generate_text(prompt, model=model, **kwargs)

