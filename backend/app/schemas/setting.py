from typing import Any

from pydantic import BaseModel, ConfigDict


class SettingValue(BaseModel):
    value: Any


class SettingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    scope: str
    key: str
    value: Any
