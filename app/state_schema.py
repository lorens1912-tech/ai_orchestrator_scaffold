from pydantic import BaseModel, Field

class WriterSettings(BaseModel):
    target_words: int = Field(500, ge=50, description="Docelowa liczba słów na wywołanie workera")
    max_tokens:  int = Field(900, ge=100, description="Twardy limit tokenów dla LLM")

class BookState(BaseModel):
    id:           str
    current_step: str = "planning"
    writer:       WriterSettings = WriterSettings()
