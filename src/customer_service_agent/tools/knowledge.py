from pydantic import BaseModel, Field


class RetrieveFAQInput(BaseModel):
    query: str = Field(min_length=2, max_length=1000)
    language: str = Field(pattern=r"^(en|vi|zh)$")


class TransferToHumanInput(BaseModel):
    reason: str = Field(min_length=2, max_length=500)
